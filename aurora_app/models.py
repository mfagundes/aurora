import os
import re
import time

from datetime import datetime
from git import Repo
from werkzeug.security import generate_password_hash, check_password_hash
from flask import url_for

from aurora_app import app
from aurora_app.constants import (ROLES, PERMISSIONS, STATUSES,
                                  BOOTSTRAP_ALERTS, PARAMETER_TYPES)
from aurora_app.database import db

FUNCTION_NAME_REGEXP = '^def (\w+)\(.*\):'


class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    password_hash = db.Column(db.String(160), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    role = db.Column(db.SmallInteger, default=ROLES['USER'])
    # Relations
    deployments = db.relationship("Deployment", backref="user")
    notifications = db.relationship("Notification", backref="user")

    def __init__(self, username=None, password=None, email=None, role=None):
        self.username = username

        if password:
            self.set_password(password)

        self.email = email
        self.role = role

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def is_authenticated(self):
        return True

    def is_active(self):
        return True

    def is_anonymous(self):
        return False

    def get_id(self):
        return unicode(self.id)

    def can(self, action):
        return action in PERMISSIONS[self.role]

    def show_role(self):
        for role, number in ROLES.iteritems():
            if number == self.role:
                return role

    def __repr__(self):
        return u'<User {0}>'.format(self.username)


class Project(db.Model):
    __tablename__ = "projects"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(32), nullable=False)
    description = db.Column(db.String(128), default='')
    repository_path = db.Column(db.String(128), default='')
    code = db.Column(db.Text(), default='')
    # Relations
    stages = db.relationship("Stage", backref="project")
    params = db.relationship("ProjectParameter", backref="project")

    def __init__(self, *args, **kwargs):
        super(Project, self).__init__(*args, **kwargs)

    def create_params(self):
        fetch_before_deploy = ProjectParameter(name='fetch_before_deploy',
                                               value='True',
                                               type=PARAMETER_TYPES['BOOL'],
                                               project_id=self.id)
        db.session.add(fetch_before_deploy)
        db.session.commit()

    def get_parameter_value(self, name):
        for parameter in self.params:
            if parameter.name == name:
                return parameter.value

    def get_name_for_path(self):
        return self.name.lower().replace(' ', '_')

    def get_path(self):
        """Returns path of project's git repository on local machine."""
        return os.path.join(app.config['AURORA_PROJECTS_PATH'],
                            self.get_name_for_path())

    def repository_folder_exists(self):
        return os.path.exists(self.get_path())

    def get_repo(self):
        if self.repository_folder_exists():
            return Repo.init(self.get_path())
        return None

    def get_branches(self):
        repo = self.get_repo()
        if repo:
            return [ref for ref in repo.refs if ref.name != 'origin/HEAD']
        return None

    def get_commits(self, branch, max_count, skip):
        repo = self.get_repo()
        if repo:
            return repo.iter_commits(branch, max_count=max_count,
                                     skip=skip)
        return None

    def get_all_commits(self, branch, skip=None):
        repo = self.get_repo()
        if repo:
            return repo.iter_commits(branch, skip=skip)
        return None

    def get_last_commit(self, branch):
        repo = self.get_repo()
        if repo:
            return repo.iter_commits(branch).next()
        return None

    def get_commits_count(self, branch):
        repo = self.get_repo()
        if repo:
            return reduce(lambda x, _: x + 1, repo.iter_commits(branch), 0)
        return None

    def fetch(self):
        repo = self.get_repo()
        if repo:
            return repo.git.fetch()

    def __repr__(self):
        return self.name


class ProjectParameter(db.Model):
    __tablename__ = "project_parameters"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), nullable=False, unique=True)
    value = db.Column(db.String(128), nullable=False)
    type = db.Column(db.SmallInteger, nullable=False)
    # Relations
    project_id = db.Column(db.Integer(), db.ForeignKey('projects.id'),
                           nullable=False)

    def set_value(self, value):
        if self.type == PARAMETER_TYPES['BOOL']:
            values = ['True', 'False']
            if value not in values:
                raise Exception('Wrong value for bool parameter.')
        elif self.type == PARAMETER_TYPES['INT']:
            try:
                int(value)
            except ValueError:
                 raise Exception('Wrong value for int parameter.')

        self.value = value

    def __init__(self, *args, **kwargs):
        super(ProjectParameter, self).__init__(*args, **kwargs)


stages_tasks_table = db.Table('stages_tasks', db.Model.metadata,
                              db.Column('stage_id', db.Integer,
                                        db.ForeignKey('stages.id')),
                              db.Column('task_id', db.Integer,
                                        db.ForeignKey('tasks.id')))


class Stage(db.Model):
    __tablename__ = "stages"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(32), nullable=False)
    code = db.Column(db.Text(), default='')
    # Relations
    project_id = db.Column(db.Integer(), db.ForeignKey('projects.id'))
    deployments = db.relationship("Deployment", backref="stage")
    tasks = db.relationship("Task",
                            secondary=stages_tasks_table,
                            backref="stages")

    def __init__(self, *args, **kwargs):
        super(Stage, self).__init__(*args, **kwargs)

    def __repr__(self):
        return u"{0} / {1}".format(self.project.name, self.name) if \
            self.project else self.name


class Task(db.Model):
    __tablename__ = "tasks"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(32), nullable=False)
    code = db.Column(db.Text(), default='')

    def __init__(self, *args, **kwargs):
        super(Task, self).__init__(*args, **kwargs)

    def get_function_name(self):
        functions_search = re.search(FUNCTION_NAME_REGEXP, self.code)
        return functions_search.group(1)

    def __repr__(self):
        return self.name


deployments_tasks_table = db.Table('deployments_tasks', db.Model.metadata,
                                   db.Column('deployment_id', db.Integer,
                                             db.ForeignKey('deployments.id')),
                                   db.Column('task_id', db.Integer,
                                             db.ForeignKey('tasks.id')))


class Deployment(db.Model):
    __tablename__ = "deployments"
    id = db.Column(db.Integer, primary_key=True)
    status = db.Column(db.SmallInteger, default=STATUSES['READY'])
    branch = db.Column(db.String(32), default='master')
    commit = db.Column(db.String(128))
    started_at = db.Column(db.DateTime(), default=datetime.now)
    finished_at = db.Column(db.DateTime())
    code = db.Column(db.Text())
    log = db.Column(db.Text())
    # Relations
    stage_id = db.Column(db.Integer(),
                         db.ForeignKey('stages.id'), nullable=False)
    user_id = db.Column(db.Integer(),
                        db.ForeignKey('users.id'), nullable=False)
    tasks = db.relationship("Task",
                            secondary=deployments_tasks_table,
                            backref="deployments")

    def get_tmp_path(self):
        return os.path.join(app.config['AURORA_TMP_DEPLOYMENTS_PATH'],
                            '{0}'.format(self.id))

    def bootstrap_status(self):
        return BOOTSTRAP_ALERTS[self.status]

    def show_status(self):
        for status, number in STATUSES.iteritems():
            if number == self.status:
                return status

    def is_running(self):
        return self.status == STATUSES['RUNNING']

    def show_tasks_list(self):
        template = '<a href="{0}">{1}</a>'
        return ', '.join([template.format(url_for('tasks.view', id=task.id),
                                          task.name) for task in self.tasks])

    def get_log_path(self):
        return os.path.join(self.get_tmp_path(), 'log')

    def get_log_lines(self):
        if self.log:
            return self.log.split('\n')

        path = os.path.join(self.get_tmp_path(), 'log')
        if os.path.exists(path):
            return open(path).readlines()

        return []

    def show_duration(self):
        delta = self.finished_at - self.started_at
        return time.strftime("%H:%M:%S", time.gmtime(delta.seconds))

    def show_commit(self):
        return "{0}".format(self.commit[:10]) if self.commit else ''

    def __init__(self, *args, **kwargs):
        super(Deployment, self).__init__(*args, **kwargs)

        self.code = [self.stage.project.code, self.stage.code]
        for task in self.stage.tasks:
            self.code.append(task.code)
        self.code = '\n'.join(self.code)


class Notification(db.Model):
    __tablename__ = "notifications"
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime(), default=datetime.now)
    message = db.Column(db.String(255), nullable=False)
    category = db.Column(db.String(32))
    action = db.Column(db.String(32))
    seen = db.Column(db.Boolean(), default=False)
    # Relations
    user_id = db.Column(db.Integer(),
                        db.ForeignKey('users.id'))

    def __init__(self, *args, **kwargs):
        super(Notification, self).__init__(*args, **kwargs)

    def __repr__(self):
        return u"<Notification #{0}>".format(self.id)

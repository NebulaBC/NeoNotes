from app import app, db
from app.model_types import GUID
from sqlalchemy.sql import func
from sqlalchemy.ext.hybrid import hybrid_property, hybrid_method, Comparator
from sqlalchemy import event
from Crypto.Cipher import AES
import binascii
import uuid
import frontmatter
import re


key = app.config['DB_ENCRYPTION_KEY']


class EncryptComparator(Comparator):
  def __eq__(self, other):
    print aes_encrypt(other)
    return func.lower(self.__clause_element__()) == func.lower(aes_encrypt(other))

def aes_encrypt(data):
  cipher = AES.new(key)
  data = data + (" " * (16 - (len(data) % 16)))
  return binascii.hexlify(cipher.encrypt(data))

def aes_decrypt(data):
  try:
    cipher = AES.new(key)
    return cipher.decrypt(binascii.unhexlify(data)).rstrip()
  except:
    # If data is not encrypted, just return it
    return data


class User(db.Model):
  uuid = db.Column(GUID, primary_key=True, index=True, unique=True, default=lambda: uuid.uuid4())
  username = db.Column(db.String(64), unique=True, nullable=False)
  password_hash = db.Column(db.String(128), nullable=False)
  notes = db.relationship('Note', lazy='dynamic', cascade='all, delete, delete-orphan')
  meta = db.relationship('Meta', lazy='dynamic', cascade='all, delete, delete-orphan')

  def __repr__(self):
    return '<User {}>'.format(self.uuid)


class Meta(db.Model):
  uuid = db.Column(GUID, primary_key=True, index=True, unique=True, default=lambda: uuid.uuid4())
  user_id = db.Column(GUID, db.ForeignKey('user.uuid'), nullable=False)
  note_id = db.Column(GUID, db.ForeignKey('note.uuid'), nullable=False)
  name_encrypted = db.Column('name', db.String)
  name_compare = db.Column(db.String)
  kind = db.Column(db.String)

  @hybrid_property
  def name(self):
    return aes_decrypt(self.name_encrypted)

  @name.setter
  def name(self, value):
    self.name_encrypted = aes_encrypt(value)

  @name.comparator
  def name(cls):
    return EncryptComparator(cls.name_encrypted)

  def __repr__(self):
    return '<Meta {}>'.format(self.uuid)


class Note(db.Model):
  uuid = db.Column(GUID, primary_key=True, index=True, unique=True, default=lambda: uuid.uuid4())
  user_id = db.Column(GUID, db.ForeignKey('user.uuid'), nullable=False)
  data = db.Column(db.String)
  title = db.Column(db.String(128), nullable=False, unique=True)
  date = db.Column(db.DateTime(timezone=True), server_default=func.now())
  is_date = db.Column(db.Boolean, default=False)
  meta = db.relationship('Meta', lazy='dynamic', cascade='all, delete, delete-orphan')

  @hybrid_property
  def text(self):
    return aes_decrypt(self.data)

  @text.setter
  def text(self, value):
    self.data = aes_encrypt(value)

  @hybrid_property
  def name(self):
    return aes_decrypt(self.title)

  @name.setter
  def name(self, value):
    self.title = aes_encrypt(value)

  @name.comparator
  def name(cls):
    return EncryptComparator(cls.title)

  def __repr__(self):
    return '<Note {}>'.format(self.uuid)

  @property
  def serialize(self):
    return {
      'uuid': self.uuid,
      'data': self.data,
      'title': self.title,
      'date': self.date,
      'is_date': self.is_date,
    }

  @property
  def serialize_full(self):
    return {
      'uuid': self.uuid,
      'data': self.data,
      'title': self.title,
      'date': self.date,
      'is_date': self.is_date,
      'tags': [x.replace('\,', ',') for x in list(set(re.split(r'(?<!\\),', (self.tags or '')))) if x],
      'projects': [x.replace('\,', ',') for x in list(set(re.split(r'(?<!\\),', (self.projects or '')))) if x],
    }


# Update title automatically
def before_change_note(mapper, connection, target):
  title = None

  data = frontmatter.loads(target.text)

  if isinstance(data.get('title'), str) and len(data.get('title')) > 0:
    title = data.get('title')

  if title and not target.is_date:
    target.name = title


# Handle changes to tasks, projects, and tags
def after_change_note(mapper, connection, target):
  tags = []
  projects = []

  data = frontmatter.loads(target.text)

  if isinstance(data.get('tags'), list):
    tags = list(set([x.replace(',', '\,') for x in data.get('tags')]))
  elif isinstance(data.get('tags'), str):
    tags = list(set(map(str.strip, data['tags'].split(','))))

  if isinstance(data.get('projects'), list):
    projects = list(set([x.replace(',', '\,') for x in data.get('projects')]))
  elif isinstance(data.get('projects'), str):
    projects = list(set(map(str.strip, data['projects'].split(','))))

  tasks = re.findall("- \[[x| ]\] (.*)", data.content)

  existing_tags = []
  existing_projects = []
  existing_tasks = []

  metas = Meta.query.filter_by(note_id=target.uuid).all()

  for meta in metas:
    if meta.kind is 'tag':
      existing_tags.append(meta)
    elif meta.kind is 'project':
      existing_projects.append(meta)
    elif meta.kind is 'task':
      existing_tasks.append(meta)

  for tag in existing_tags:
    if tag.name not in tags:
      connection.execute(
        'DELETE FROM meta WHERE uuid = ?',
        '{}'.format(tag.uuid).replace('-', '')
      )
    else:
      tags.remove(tag.name)

  for tag in tags:
    connection.execute(
      'INSERT INTO meta (uuid, user_id, note_id, name, kind) VALUES (?, ?, ?, ?, ?)',
      '{}'.format(uuid.uuid4()).replace('-', ''),
      '{}'.format(target.user_id).replace('-', ''),
      '{}'.format(target.uuid).replace('-', ''),
      aes_encrypt(tag),
      'tag'
    )

  for project in existing_projects:
    if project.name not in projects:
      connection.execute(
        'DELETE FROM meta WHERE uuid = ?',
        '{}'.format(project.uuid).replace('-', '')
      )
    else:
      projects.remove(project.name)

  for project in projects:
    connection.execute(
      'INSERT INTO meta (uuid, user_id, note_id, name, kind) VALUES (?, ?, ?, ?, ?)',
      '{}'.format(uuid.uuid4()).replace('-', ''),
      '{}'.format(target.user_id).replace('-', ''),
      '{}'.format(target.uuid).replace('-', ''),
      aes_encrypt(project),
      'project'
    )

  for task in existing_tasks:
    if task.name not in tasks:
      connection.execute(
        'DELETE FROM meta WHERE uuid = ?',
        '{}'.format(task.uuid).replace('-', '')
      )
    else:
      task.remove(task.name)

  for task in tasks:
    encrypted_task = aes_encrypt(task)

    connection.execute(
      'INSERT INTO meta (uuid, user_id, note_id, name, name_compare, kind) VALUES (?, ?, ?, ?, ?, ?)',
      '{}'.format(uuid.uuid4()).replace('-', ''),
      '{}'.format(target.user_id).replace('-', ''),
      '{}'.format(target.uuid).replace('-', ''),
      encrypted_task,
      encrypted_task,
      'task'
    )

def before_update_task(mapper, connection, target):
  if target.kind != 'task':
    return

  if target.name_encrypted == target.name_compare:
    return

  note = Note.query.get(target.note_id)

  if not note:
    return

  note_data = aes_encrypt(note.text.replace(aes_decrypt(target.name_compare), target.name))

  connection.execute(
    'UPDATE note SET data = ?',
    note_data,
  )

  target.name_compare = target.name_encrypted


event.listen(Note, 'before_insert', before_change_note)
event.listen(Note, 'before_update', before_change_note)
event.listen(Note, 'after_insert', after_change_note)
event.listen(Note, 'after_update', after_change_note)
event.listen(Meta, 'before_update', before_update_task)

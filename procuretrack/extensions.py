"""
Flask extensions, created here without an app and connected in create_app().
Import `db` from here wherever you need the database.
"""

from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect

db = SQLAlchemy()
migrate = Migrate()
csrf = CSRFProtect()

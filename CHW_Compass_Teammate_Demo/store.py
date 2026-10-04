"""Small encrypted, single-process SQLite snapshot store for synthetic demonstration data.

SQLite works in memory; after each write the whole database is AES-encrypted with
Fernet. Not a substitute for audited mobile key management or production auth.
"""
from __future__ import annotations
import base64
import os
from pathlib import Path
import sqlite3
import threading
from contextlib import contextmanager
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = b"CHWCOMPASS1"  # file format prefix

class EncryptedStore:
    def __init__(self, path: str | Path, passphrase: str):
        if not passphrase or len(passphrase) < 12:
            raise ValueError("Set COMPASS_PASSPHRASE to at least 12 characters")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.passphrase = passphrase.encode()
        self.lock = threading.RLock()
        self._batch_active = False
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        if self.path.exists():
            raw = self.path.read_bytes()
            if not raw.startswith(MAGIC):
                raise ValueError("Not a CHW Compass encrypted database")
            salt = raw[len(MAGIC):len(MAGIC)+16]
            self.salt = salt
            self.cipher = self._fernet()
            try:
                self.db.deserialize(self.cipher.decrypt(raw[len(MAGIC)+16:]))
            except InvalidToken as ex:
                raise ValueError("Cannot unlock data: wrong passphrase or corrupt database") from ex
        else:
            self.salt = os.urandom(16)
            self.cipher = self._fernet()
        self.db.execute("PRAGMA foreign_keys=ON")

    def _fernet(self):
        kdf = Scrypt(salt=self.salt,length=32,n=2**14,r=8,p=1)
        return Fernet(base64.urlsafe_b64encode(kdf.derive(self.passphrase)))

    def save(self):
        with self.lock:
            data = MAGIC + self.salt + self.cipher.encrypt(self.db.serialize())
            temp = self.path.with_name(self.path.name + ".tmp")
            temp.write_bytes(data)
            os.chmod(temp, 0o600)
            os.replace(temp, self.path)

    @contextmanager
    def batch(self):
        """Single-process atomic batch. One encrypted snapshot on success.

        Do not call executescript inside this context (SQLite executescript
        implicitly commits transactions). An exception rolls back all writes.
        """
        with self.lock:
            if self._batch_active:
                raise RuntimeError('Nested batches are not supported')
            self.db.execute('BEGIN IMMEDIATE')
            self._batch_active = True
            try:
                yield self
            except BaseException:
                self.db.rollback()
                raise
            else:
                self.db.commit()
                self.save()
            finally:
                self._batch_active = False

    def execute(self, sql, args=()):
        with self.lock:
            cur = self.db.execute(sql,args)
            if not self._batch_active:
                self.db.commit()
                self.save()
            return cur.lastrowid

    def many(self, sql, data):
        with self.lock:
            self.db.executemany(sql,data)
            if not self._batch_active:
                self.db.commit()
                self.save()

    def script(self, sql):
        if self._batch_active:
            raise RuntimeError('Cannot executescript inside a batch')
        with self.lock:
            self.db.executescript(sql)
            self.db.commit()
            self.save()

    def all(self, sql, args=()):
        with self.lock:
            return [dict(x) for x in self.db.execute(sql,args).fetchall()]

    def one(self, sql, args=()):
        with self.lock:
            row=self.db.execute(sql,args).fetchone()
            return dict(row) if row else None

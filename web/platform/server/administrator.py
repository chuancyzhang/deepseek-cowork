"""One-time deployment setup and a separate, web-only platform administrator."""
import logging
import re
import threading
import time

from sqlalchemy import select

from .database import AuthSource, Installation, LocalAdministrator, Member, Platform
from .security import PlatformError, digest, equal, password_hash, token

log = logging.getLogger(__name__)


class AdministratorAccess:
    def __init__(self, settings, database):
        self.settings, self.database = settings, database
        self.lock = threading.RLock()

    def available(self, db):
        state = db.get(Installation, 1)
        return not (state and state.completed) and not db.scalar(select(Member.id).limit(1)) and not db.scalar(select(AuthSource.id).limit(1)) and not db.get(LocalAdministrator, 1)

    def prepare(self):
        with self.lock, self.database.transaction() as db:
            if db.get(Installation, 1) is None:
                db.add(Installation(id=1))
                db.flush()
            if self.available(db):
                log.info('installation.pending')

    def status(self):
        with self.database.transaction() as db:
            return {'required': self.available(db), 'administrator_login': db.get(LocalAdministrator, 1) is not None}

    @staticmethod
    def throttle(state):
        if state.locked_until > time.time():
            raise PlatformError('login_throttled', '尝试次数过多，请 5 分钟后重试。', 429)

    def reject(self, db, state, action):
        state.failures += 1
        if state.failures >= 5:
            state.locked_until = time.time() + 300
            state.failures = 0
        self.database.audit(db, action, outcome='rejected')
        log.warning('%s outcome=rejected', action)

    def initialize(self, payload):
        username, password = payload.get('username', ''), payload.get('password', '')
        if not isinstance(username, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{2,63}', username):
            raise PlatformError('invalid_administrator', '管理员账号需为 3–64 位字母、数字、点、横线或下划线。')
        if not isinstance(password, str) or not 12 <= len(password) <= 256:
            raise PlatformError('invalid_password', '密码需为 12–256 个字符。')
        member_id = None
        with self.lock, self.database.transaction() as db:
            db.scalar(select(Platform).where(Platform.id == 1).with_for_update())
            state = db.get(Installation, 1)
            if not self.available(db):
                raise PlatformError('setup_completed', '平台已完成初始化，请使用已有管理员账号登录。', 409)
            salt = token()
            member = Member(provider_id='__platform_admin__', subject=username, name=username, role='admin')
            db.add(member)
            db.flush()
            db.add(LocalAdministrator(id=1, member_id=member.id, username=username,
                                      password_hash=password_hash(password, salt), salt=salt))
            state.completed, state.failures, state.locked_until = True, 0, 0
            member_id = member.id
            self.database.audit(db, 'installation.completed', member_id=member_id)
        log.info('installation.completed member=%s', member_id)
        return member_id

    def login(self, payload):
        username, password = payload.get('username'), payload.get('password')
        if not isinstance(username, str) or not isinstance(password, str) or len(username) > 64 or len(password) > 256:
            raise PlatformError('invalid_credentials', '管理员账号或密码不正确。', 401)
        member_id = None
        with self.lock, self.database.transaction() as db:
            state = db.scalar(select(Installation).where(Installation.id == 1).with_for_update())
            self.throttle(state)
            account = db.get(LocalAdministrator, 1)
            computed = password_hash(password, account.salt if account else 'no-local-administrator')
            member = db.get(Member, account.member_id) if account else None
            if not account or not equal(digest(username), digest(account.username)) or not equal(computed, account.password_hash) or not member or not member.enabled or member.role != 'admin':
                self.reject(db, state, 'administrator.login_failed')
            else:
                state.failures, state.locked_until = 0, 0
                member.last_login_at = time.time()
                member_id = member.id
        if not member_id:
            raise PlatformError('invalid_credentials', '管理员账号或密码不正确，或账号已停用。', 401)
        return member_id

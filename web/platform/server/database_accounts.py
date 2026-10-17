"""Administrator-managed employee credentials; no public registration or plaintext passwords."""
import logging
import re
import threading
import time

from sqlalchemy import delete, select, update

from .database import AuthSource, DatabaseAccount, LocalAdministrator, LoginSession, LoginTransaction, Member, Platform, uid
from .security import PlatformError, equal, password_hash, token

log = logging.getLogger(__name__)


def account_view(account, member):
    return {'id': account.member_id, 'username': account.username, 'name': member.name,
            'enabled': member.enabled, 'role': member.role, 'revision': account.revision,
            'member_revision': member.revision}


class DatabaseAccounts:
    def __init__(self, database):
        self.database = database
        self.lock = threading.RLock()

    @staticmethod
    def source(db, provider_id):
        source = db.get(AuthSource, provider_id)
        if not source or source.mode != 'database':
            raise PlatformError('source_unavailable', '平台账号密码认证来源不存在。', 404)
        return source

    def list(self, provider_id):
        with self.database.transaction() as db:
            self.source(db, provider_id)
            return [account_view(account, member) for account, member in db.execute(
                select(DatabaseAccount, Member).join(Member, Member.id == DatabaseAccount.member_id)
                .where(DatabaseAccount.provider_id == provider_id).order_by(DatabaseAccount.username))]

    def save(self, provider_id, payload, actor, member_id=None):
        password, name = payload.get('password', ''), payload.get('name', '')
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            raise PlatformError('invalid_account', '请填写 1–80 字的成员姓名。')
        if not isinstance(password, str) or (not member_id or password) and not 12 <= len(password) <= 256:
            raise PlatformError('invalid_password', '密码需为 12–256 个字符。')
        enabled = payload.get('enabled', True)
        if type(enabled) is not bool:
            raise PlatformError('invalid_account', '账号状态无效。')
        with self.lock, self.database.transaction() as db:
            db.scalar(select(Platform).where(Platform.id == 1).with_for_update())
            self.source(db, provider_id)
            account = db.get(DatabaseAccount, member_id) if member_id else None
            if member_id:
                if not account or account.provider_id != provider_id:
                    raise PlatformError('not_found', '账号不存在。', 404)
                member = db.scalar(select(Member).where(Member.id == member_id).with_for_update())
                founder = db.get(LocalAdministrator, 1)
                if member.role == 'admin' and founder and founder.member_id != actor:
                    raise PlatformError('founder_required', '管理员账号的密码和状态只能由首位管理员管理。', 403)
                if payload.get('revision') != account.revision or payload.get('member_revision') != member.revision:
                    raise PlatformError('conflict', '账号已被其他操作修改，请刷新后核对。', 409)
                if payload.get('username', account.username) != account.username:
                    raise PlatformError('invalid_account', '账号名称不能更改；成员身份与授权保持绑定。')
                # Follow the same last-administrator invariant as member management.
                if member.role == 'admin' and not enabled:
                    from sqlalchemy import func
                    if db.scalar(select(func.count()).select_from(Member).where(Member.enabled, Member.role == 'admin')) <= 1:
                        raise PlatformError('last_admin', '不能停用最后一位管理员。', 409)
                account.revision += 1
                member.revision += 1
            else:
                username = payload.get('username', '')
                if not isinstance(username, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{2,63}', username):
                    raise PlatformError('invalid_account', '账号需为 3–64 位字母、数字、点、横线或下划线。')
                if db.scalar(select(DatabaseAccount.member_id).where(DatabaseAccount.provider_id == provider_id, DatabaseAccount.username == username)):
                    raise PlatformError('account_exists', '此认证来源中已存在同名账号，请使用其他账号名称。', 409)
                member_id = uid()
                member = Member(id=member_id, provider_id=provider_id, subject=member_id, name=name.strip(), role='member')
                account = DatabaseAccount(member_id=member_id, provider_id=provider_id, username=username)
                db.add_all([member, account])
            member.name, member.enabled = name.strip(), enabled
            if password:
                account.salt = token()
                account.password_hash = password_hash(password, account.salt)
                account.failures, account.locked_until = 0, 0
            if password or not enabled:
                db.execute(delete(LoginSession).where(LoginSession.member_id == member_id))
                db.execute(update(LoginTransaction).where(LoginTransaction.member_id == member_id,
                    LoginTransaction.status.in_(['pending', 'exchanging', 'ready'])).values(status='cancelled'))
            db.flush()
            self.database.audit(db, 'database_account.saved', member_id=actor, target=member_id)
            result = account_view(account, member)
        log.info('database_account.saved member=%s password_changed=%s', member_id, bool(password))
        return result

    def authenticate(self, provider_id, username, password):
        if not isinstance(username, str) or not isinstance(password, str) or not 1 <= len(username) <= 64 or not 1 <= len(password) <= 256:
            raise PlatformError('invalid_credentials', '账号或密码不正确。', 401)
        identity = None
        with self.lock, self.database.transaction() as db:
            account = db.scalar(select(DatabaseAccount).where(DatabaseAccount.provider_id == provider_id,
                                DatabaseAccount.username == username).with_for_update())
            if account and account.locked_until > time.time():
                raise PlatformError('account_locked', '此账号尝试次数过多，请 5 分钟后重试或请管理员重设密码。', 429)
            computed = password_hash(password, account.salt if account else 'unknown-database-account')
            member = db.get(Member, account.member_id) if account else None
            if not account or not member or not member.enabled or not equal(computed, account.password_hash):
                if account:
                    account.failures += 1
                    if account.failures >= 5:
                        account.failures, account.locked_until = 0, time.time() + 300
                self.database.audit(db, 'database_account.login_failed', target=account.member_id if account else provider_id, outcome='invalid_credentials')
            else:
                account.failures, account.locked_until = 0, 0
                identity = {'subject': member.subject, 'name': member.name, 'email': member.email,
                            '_member_revision': member.revision}
        if not identity:
            log.warning('database_account.login_failed provider=%s', provider_id)
            raise PlatformError('invalid_credentials', '账号或密码不正确，或账号已停用。', 401)
        return identity

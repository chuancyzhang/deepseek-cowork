"""Desktop file discovery for the shared public enterprise connection contract."""
from pathlib import Path

from web.platform.server.connection_contract import (
    CONNECTION_FILE, SUPPORTED_CONNECTION_SCHEMAS, SUPPORTED_PLATFORM_API_VERSIONS,
    MAX_CONNECTION_BYTES, FIELDS, EnterpriseConfigurationError, validate_connection,
    parse_connection, check_update_connection,
)


def read_connection(base_dir=None, *, development=False):
    if base_dir is None:
        from core.env_utils import get_base_dir
        base_dir = get_base_dir()
    path = Path(base_dir) / CONNECTION_FILE
    if not path.exists():
        return None
    with path.open("rb") as handle:
        return parse_connection(handle.read(MAX_CONNECTION_BYTES + 1), development=development)


"""Proxy export for pinterest_uploader service."""
from redsun.services.pinterest_uploader import (
    check_pinterest_status,
    get_or_map_boards,
    publish_project_pins,
    resolve_board_id_for_pin,
)

__all__ = [
    "check_pinterest_status",
    "get_or_map_boards",
    "publish_project_pins",
    "resolve_board_id_for_pin",
]


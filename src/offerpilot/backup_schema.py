"""Exact compatibility for the two verified pre-fix compensation trigger layouts.

Do not normalize arbitrary SQL or whitespace. These frozen full statements only
bridge the 0029/0030 formatting drift; every other schema byte remains checked.
Future trigger logic changes must not broaden this mapping implicitly.
"""
from typing import Any

_CANONICAL_SQL = {
    'trg_write_operation_compensation_insert': """CREATE TRIGGER trg_write_operation_compensation_insert
            BEFORE INSERT ON write_operations
            WHEN NEW.operation_role = 'compensation'
            BEGIN
                SELECT CASE WHEN NOT EXISTS (
          SELECT 1 FROM write_operations parent
          WHERE parent.id = NEW.parent_operation_id
            AND parent.operation_role = 'primary'
            AND parent.status = 'committed'
            AND parent.terminal_payload_sha256 = NEW.parent_terminal_payload_sha256
            AND length(NEW.parent_terminal_payload_sha256) = 71
            AND substr(NEW.parent_terminal_payload_sha256,1,7) = 'sha256:'
            AND substr(NEW.parent_terminal_payload_sha256,8) NOT GLOB '*[^0-9a-f]*'
            AND (
              (parent.adapter_kind = 'typed' AND (
                (parent.tool_name = 'update_application_status'
                 AND NEW.tool_name = 'undo:update_application_status') OR
                (parent.tool_name = 'create_application'
                 AND NEW.tool_name = 'undo:create_application') OR
                (parent.tool_name = 'create_application_event'
                 AND NEW.tool_name = 'undo:create_application_event') OR
                (parent.tool_name = 'add_note' AND NEW.tool_name = 'undo:add_note') OR
                (parent.tool_name = 'create_offer' AND NEW.tool_name = 'undo:create_offer')
              )) OR
              (parent.adapter_kind = 'product_action' AND (
                (parent.tool_name = 'confirm_interview_story'
                 AND NEW.tool_name = 'undo:confirm_interview_story') OR
                (parent.tool_name = 'save_review_readiness_signal'
                 AND NEW.tool_name = 'undo:save_review_readiness_signal')
              ))
            )
        )
                  THEN RAISE(ABORT, 'invalid compensation parent') END;
            END""",
    'trg_write_operation_compensation_update': """CREATE TRIGGER trg_write_operation_compensation_update
            BEFORE UPDATE OF parent_operation_id, parent_terminal_payload_sha256,
                             operation_role, tool_name ON write_operations
            WHEN NEW.operation_role = 'compensation'
            BEGIN
                SELECT CASE WHEN NOT EXISTS (
          SELECT 1 FROM write_operations parent
          WHERE parent.id = NEW.parent_operation_id
            AND parent.operation_role = 'primary'
            AND parent.status = 'committed'
            AND parent.terminal_payload_sha256 = NEW.parent_terminal_payload_sha256
            AND length(NEW.parent_terminal_payload_sha256) = 71
            AND substr(NEW.parent_terminal_payload_sha256,1,7) = 'sha256:'
            AND substr(NEW.parent_terminal_payload_sha256,8) NOT GLOB '*[^0-9a-f]*'
            AND (
              (parent.adapter_kind = 'typed' AND (
                (parent.tool_name = 'update_application_status'
                 AND NEW.tool_name = 'undo:update_application_status') OR
                (parent.tool_name = 'create_application'
                 AND NEW.tool_name = 'undo:create_application') OR
                (parent.tool_name = 'create_application_event'
                 AND NEW.tool_name = 'undo:create_application_event') OR
                (parent.tool_name = 'add_note' AND NEW.tool_name = 'undo:add_note') OR
                (parent.tool_name = 'create_offer' AND NEW.tool_name = 'undo:create_offer')
              )) OR
              (parent.adapter_kind = 'product_action' AND (
                (parent.tool_name = 'confirm_interview_story'
                 AND NEW.tool_name = 'undo:confirm_interview_story') OR
                (parent.tool_name = 'save_review_readiness_signal'
                 AND NEW.tool_name = 'undo:save_review_readiness_signal')
              ))
            )
        )
                  THEN RAISE(ABORT, 'invalid compensation parent') END;
            END""",
}

_CANONICAL_PAIR = "(parent.tool_name = 'add_note' AND NEW.tool_name = 'undo:add_note') OR\n                (parent.tool_name = 'create_offer' AND NEW.tool_name = 'undo:create_offer')"
_LEGACY_PAIR = "(parent.tool_name = 'add_note' AND NEW.tool_name = 'undo:add_note')\n                \n                OR (parent.tool_name = 'create_offer'\n                    AND NEW.tool_name = 'undo:create_offer')\n            "
_LEGACY_SQL = {
    name: sql.replace(_CANONICAL_PAIR, _LEGACY_PAIR)
    for name, sql in _CANONICAL_SQL.items()
}


def canonical_schema_row(row: tuple[Any, ...]) -> tuple[Any, ...]:
    kind, name, table, sql = row
    if (kind == "trigger" and table == "write_operations"
            and name in _LEGACY_SQL and sql == _LEGACY_SQL[name]):
        return kind, name, table, _CANONICAL_SQL[name]
    return row

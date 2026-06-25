"""Partner CLI — thin wrapper delegating to the modular cli package.

All actual command logic lives in partner/cli/*.py.
This file exists for backward compatibility with existing imports.
"""

from .cli.common import (
    # ANSI constants
    C_RESET, C_BOLD, C_DIM, C_CYAN, C_GREEN, C_YELLOW, C_RED,
    # Utilities
    CREATION_FLAGS,
    _cli_txt, _fmt_bool, _fmt_optional, _print_kv,
    _print_commands, _print_help_menu,
    _resolve_qq_config,
    get_workspace, _resolve_runtime_workspace, _root_workspace_if_different,
    _load_manager_module, _get_default_instance_id, _save_default_instance_id,
    _bot_start, _bot_stop, _auto_start_instance,
    _load_global_cfg, _save_global_cfg,
    _resolve_config_workspace, _load_cfg_for_workspace, _ensure_agent_cfg,
    _server_tunnel_command,
    _cmd_queue_clear, _cmd_config_set,
    # Command handlers
)

from .cli.main import (
    main,
    cmd_setup, cmd_status, cmd_doctor, cmd_help, cmd_default,
    cmd_bot, cmd_short_bot, cmd_instance, cmd_showcase,
    cmd_update, cmd_server, cmd_ollama,
)

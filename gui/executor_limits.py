"""Room output budgets for our pinned executor adapter; upstream defaults stay intact."""

MAX_OUTPUT_TOKENS=128000


def validate_output_tokens(value):
    if type(value) is not int or not 1<=value<=MAX_OUTPUT_TOKENS:
        raise ValueError(f'执行器输出上限必须是 1 至 {MAX_OUTPUT_TOKENS} 的整数。')
    return value


def install_output_limits(*,provider_only=False):
    """Extend only the output validator in our MCP and hardened HTTP child.

    Upstream uses its 16,384 default as a validation ceiling. Keep its default
    and all isolation/mutation/response-byte limits; accept our room's explicit
    output setting (up to 128,000) in both processes. DeepSeek currently allows
    393,216; this adapter deliberately matches the application's bounded range.
    """
    from deepseek_mcp import provider_child
    provider_child.validate_max_output_tokens=validate_output_tokens
    # The hardened child intentionally has no HOME/USERPROFILE. Config imports
    # workspace/recovery paths; those belong to the MCP host, never the HTTP child.
    if not provider_only:
        from deepseek_mcp import config
        config._validate_max_output_tokens=validate_output_tokens

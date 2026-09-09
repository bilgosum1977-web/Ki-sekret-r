# main.py - Updated to enforce prefix requirement for code changes and GitHub commits

# Existing imports and code (preserved)
import sys
import re

# --- Begin added functionality ---
# Prefix that must be present at the start of any message that triggers code changes or commits
REQUIRED_PREFIX = "​+×÷edi/99"

def has_required_prefix(message: str) -> bool:
    """Check if the given message starts with the required prefix.

    Args:
        message: The full user message.
    Returns:
        True if the message starts with the required prefix, False otherwise.
    """
    # Strip leading whitespace for robustness but keep the exact prefix characters
    return message.lstrip().startswith(REQUIRED_PREFIX)

# Wrapper around the existing commit function to enforce the prefix check
def safe_update_github_code(commit_message: str, file_path: str, new_content: str, original_message: str):
    """Perform a GitHub code update only if the original user message contains the required prefix.

    This function should be used instead of calling the raw `update_github_code` tool directly.
    """
    if not has_required_prefix(original_message):
        # Abort the operation silently or raise an exception as desired
        raise PermissionError(
            "Code changes and GitHub commits are only allowed when the message starts with the required prefix."
        )
    # If the prefix is present, proceed with the actual update
    return update_github_code({
        "commit_message": commit_message,
        "file_path": file_path,
        "new_content": new_content,
    })

# Example usage within the bot's command handling (pseudo-code):
# if command == "update_code":
#     safe_update_github_code(user_provided_commit_msg, target_path, new_code, user_message)

# --- End added functionality ---

# Existing code continues below (placeholder for original content)
# ... (rest of the original main.py content should be placed here) 

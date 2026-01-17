from core.interaction import ask_user

def ask_user_confirmation(message):
    """
    Ask the user for confirmation.
    
    Args:
        message (str): The message to display.
    """
    if ask_user(message):
        return "User confirmed (Yes)."
    else:
        return "User denied (No)."

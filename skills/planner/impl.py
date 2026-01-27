from core.interaction import ask_user

def propose_plan(title, steps, reasoning):
    """
    Display a plan to the user and request approval.
    """
    # Format the plan for display
    plan_text = f"## {title}\n\n"
    plan_text += f"**Reasoning:** {reasoning}\n\n"
    plan_text += "**Steps:**\n"
    for i, step in enumerate(steps, 1):
        plan_text += f"{i}. {step}\n"
        
    plan_text += "\n\n**Do you approve this plan?**"
    
    # Ask for confirmation
    result = ask_user(plan_text)
    
    if result is True:
        return "User approved the plan."
    elif result is False:
        return "User rejected the plan."
    else:
        return f"User replied: {result}"

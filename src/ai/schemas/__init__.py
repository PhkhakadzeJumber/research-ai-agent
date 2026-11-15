"""Agent output shapes (doc 10 §12): what each agent must return, validated with Pydantic.

These are agent contracts, not database tables and not API responses (doc 11 §2):
the LLM fills these shapes, code verifies them and adds IDs and trust fields before
anything is stored. Field descriptions are written as instructions, because they are
shown to the model as part of the output schema.
"""

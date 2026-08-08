"""
Structured output prompts for the Analyzer Agent.

PHASE 0 SCAFFOLD — NOT IMPLEMENTED.
Implementation begins in Phase 3.

All prompts must:
1. Wrap raw SQL and external plan descriptions in <untrusted_input> tags
2. Specify JSON output schema explicitly (structured output mode)
3. Never ask the LLM for risk classification or reversibility determination
4. Never ask the LLM to generate SQL (including rollback SQL)

Example template structure (do not implement yet):

INTENT_SUMMARY_PROMPT = '''
You are analyzing a database operation for a consequence audit system.
Extract the human-readable intent from this SQL operation.

<untrusted_input>
{raw_sql}
</untrusted_input>

Respond with a JSON object matching this schema:
{json_schema}

Do not assess risk. Do not classify reversibility. Extract intent only.
'''
"""

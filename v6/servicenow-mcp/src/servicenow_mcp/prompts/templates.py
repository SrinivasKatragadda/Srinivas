"""
ServiceNow MCP Prompt Templates.
Pre-built prompt workflows for incident investigation, change planning, and script generation.
"""

from mcp.server.mcpserver import MCPServer


def register_prompts(server: MCPServer):
    @server.prompt("investigate_incident")
    def investigate_incident_prompt(incident_number: str) -> str:
        """Prompt to guide root-cause investigation for a ServiceNow incident."""
        return (
            f"You are a Senior ServiceNow ITSM and SRE Specialist. Investigate incident '{incident_number}':\n\n"
            "Steps to perform:\n"
            f"1. Call `get_incident(number_or_sysid='{incident_number}')` to inspect short_description, priority, caller, CI, and state.\n"
            "2. If an affected CI is attached, call `list_ci_relationships(ci_sys_id=...)` to check dependencies.\n"
            "3. Search for recent changes on the same CI using `list_change_requests(limit=5)`.\n"
            "4. Search for related knowledge articles using `search_knowledge(query=...)`.\n"
            "5. Provide an assessment with:\n"
            "   - Incident Summary & Impact\n"
            "   - Likely Root Cause\n"
            "   - Recommended Next Steps or Resolution\n"
        )

    @server.prompt("create_change_plan")
    def create_change_plan_prompt(change_title: str, target_system: str) -> str:
        """Prompt to construct a structured change request plan."""
        return (
            f"You are a ServiceNow Change Management Architect. Help plan the change '{change_title}' on '{target_system}':\n\n"
            "Requirements:\n"
            "1. Propose change classification (Normal vs Standard vs Emergency) and justification.\n"
            "2. Formulate a comprehensive Step-by-Step Implementation Plan.\n"
            "3. Formulate a Backout / Rollback Plan in case of failure.\n"
            "4. Formulate a Test / Validation Plan.\n"
            "5. Assess risk factors and list prerequisite CIs or approvals needed.\n"
            "6. Call `create_change_request` with the drafted details when user approves.\n"
        )

    @server.prompt("generate_business_rule")
    def generate_business_rule_prompt(table: str, requirement: str) -> str:
        """Prompt to generate production-ready ServiceNow Business Rule code."""
        return (
            f"You are a Certified ServiceNow Application Developer. Write a Business Rule for table '{table}':\n\n"
            f"Requirement: {requirement}\n\n"
            "Best practice guidelines:\n"
            "- Specify 'When' to run (before, after, async, display).\n"
            "- Wrap logic inside an immediately invoked function expression: `(function executeRule(current, previous /*null when async*/) { ... })(current, previous);`\n"
            "- Never call `current.update()` inside a Business Rule.\n"
            "- Use `current.setAbortAction(true)` for validation rejections.\n"
            "- Use `gs.addErrorMessage()` or `gs.addInfoMessage()` appropriately.\n"
            "- Inspect existing rules using `get_business_rules(table='{table}')` before creating.\n"
        )

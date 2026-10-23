"""
Automated Test Framework (ATF) Tools.
Provides queries for test suites, test cases, steps, and test execution results.
"""

from typing import Any, Dict, List, Optional
from mcp.server.mcpserver import MCPServer
from servicenow_mcp.client import default_client, ServiceNowClient


def register_atf_tools(server: MCPServer, client: Optional[ServiceNowClient] = None):
    active_client = client or default_client

    @server.tool(
        name="list_atf_suites",
        description="List Automated Test Framework (ATF) test suites from sys_atf_test_suite.",
    )
    def list_atf_suites(
        name: str = "",
        active_only: bool = True,
        limit: int = 20,
    ) -> Dict[str, Any]:
        """
        Args:
            name: Filter by suite name.
            active_only: Only active suites.
            limit: Maximum count to return.
        """
        parts = []
        if active_only:
            parts.append("active=true")
        if name:
            parts.append(f"nameLIKE{name}")

        query = "^".join(parts) if parts else ""
        suites = active_client.query_records(
            table="sys_atf_test_suite",
            query=query,
            fields="sys_id,name,description,active,sys_updated_on",
            limit=limit,
            display_value="true",
        )
        return {"count": len(suites), "suites": suites}

    @server.tool(
        name="list_atf_tests",
        description="List ATF test cases from sys_atf_test.",
    )
    def list_atf_tests(
        suite_sys_id: str = "",
        name: str = "",
        active_only: bool = True,
        limit: int = 20,
    ) -> Dict[str, Any]:
        """
        Args:
            suite_sys_id: Filter by parent suite sys_id.
            name: Search by test name.
            active_only: Only active tests.
            limit: Maximum count to return.
        """
        parts = []
        if active_only:
            parts.append("active=true")
        if suite_sys_id:
            parts.append(f"sys_atf_test_suite={suite_sys_id}")
        if name:
            parts.append(f"nameLIKE{name}")

        query = "^".join(parts) if parts else ""
        tests = active_client.query_records(
            table="sys_atf_test",
            query=query,
            fields="sys_id,name,description,active,sys_updated_on",
            limit=limit,
            display_value="true",
        )
        return {"count": len(tests), "tests": tests}

    @server.tool(
        name="get_atf_test_results",
        description="Get recent ATF test run execution results and pass/fail statuses.",
    )
    def get_atf_test_results(
        test_sys_id: str = "",
        status: str = "",
        limit: int = 15,
    ) -> Dict[str, Any]:
        """
        Args:
            test_sys_id: Specific test sys_id.
            status: 'success', 'failure', 'error', etc.
            limit: Maximum results to return.
        """
        parts = []
        if test_sys_id:
            parts.append(f"test={test_sys_id}")
        if status:
            parts.append(f"status={status}")

        query = "^".join(parts) if parts else ""
        results = active_client.query_records(
            table="sys_atf_test_result",
            query=query,
            fields="sys_id,test,status,execution_time,start_time,end_time,output",
            limit=limit,
            order_by="-start_time",
            display_value="true",
        )
        return {"count": len(results), "results": results}

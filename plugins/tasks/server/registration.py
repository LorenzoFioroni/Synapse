"""Register the task tools with their existing names, schemas and readable errors."""

from functools import wraps

from backend.mcp.protocol import Tool, ToolError
from backend.plugins import Plugin, PluginError

from .tools import TaskToolsMixin


class _Registry:
    def __init__(self, plugin: Plugin):
        self.plugin = plugin

    def add(self, tool: Tool) -> None:
        @wraps(tool.fn)
        def call(**kwargs):
            try:
                return tool.fn(**kwargs)
            except ToolError as e:
                raise PluginError(str(e)) from e

        self.plugin.mcp_tool(
            tool.description, name=tool.name, title=tool.title, schema=tool.input_schema, read_only=tool.read_only
        )(call)


def register(plugin: Plugin) -> None:
    server = _Registry(plugin)
    t = TaskToolsMixin(plugin.synapse)
    task_arg = {"type": "string", "description": 'The task\'s id (as in its {{task:ID}} line, e.g. "k7f3q2mx").'}
    fields = {
        "due": {"type": "string", "description": 'Due date YYYY-MM-DD ("" removes it).'},
    }
    server.add(
        Tool(
            "list_tasks",
            "List the user's tasks",
            "The user's tasks (to-dos): title, status, due date, sub-task progress and the notes that show them (as "
            "{{task:ID}} lines). Open tasks only unless include_done. query filters like the app's Tasks page: words, "
            '-word, "phrase", is:open / is:done, due:today / due:overdue / due:week / due:none / due:<2026-11-01, '
            'note:"title", has:body, has:subtasks.',
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Words and/or filters (optional)."},
                    "include_done": {"type": "boolean", "description": "Also list done tasks (default false)."},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200, "description": "Default 100."},
                },
            },
            t.list_tasks,
        )
    )
    server.add(
        Tool(
            "get_task",
            "Read a task",
            "One task with its details (Markdown), sub-tasks, parent tasks and the notes that show it.",
            {"type": "object", "properties": {"task": task_arg}, "required": ["task"]},
            t.get_task,
        )
    )
    server.add(
        Tool(
            "create_task",
            "Create a task",
            "Create a task (marked as made by you, the AI app; the user can undo it). With note= the task's line {{task:ID}} is "
            "added to that note: after after_line, else after the note's last task, else at the end. With parent= it "
            "becomes a sub-task. Use only when the user asks for tasks to be made.",
            {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "One line; inline Markdown and $math$ work."},
                    "details": {"type": "string", "description": "Markdown body (optional)."},
                    **fields,
                    "parent": {"type": "string", "description": "Id of the parent task, for a sub-task."},
                    "note": {"type": "string", "description": "Note (path, file name or title) that shows the task."},
                    "after_line": {"type": "integer", "description": "Line of that note after which it goes."},
                },
                "required": ["title"],
            },
            t.create_task,
            read_only=False,
        )
    )
    server.add(
        Tool(
            "update_task",
            "Change a task",
            "Change a task's title, details, status (open/done), due date or parent. Recorded as "
            "your change (the AI app's); the user can undo it. Use only when the user asks.",
            {
                "type": "object",
                "properties": {
                    "task": task_arg,
                    "title": {"type": "string"},
                    "details": {"type": "string", "description": "The whole new Markdown body."},
                    "status": {"type": "string", "enum": ["open", "done"]},
                    **fields,
                    "parent": {"type": "string", "description": 'New parent task id ("" for the top level).'},
                    "with_subtasks": {"type": "boolean", "description": "Give its sub-tasks the same status too."},
                },
                "required": ["task"],
            },
            t.update_task,
            read_only=False,
        )
    )
    server.add(
        Tool(
            "delete_task",
            "Delete a task",
            "Move a task and its sub-tasks to the trash (the user can restore them). Use only when the user asks.",
            {"type": "object", "properties": {"task": task_arg}, "required": ["task"]},
            t.delete_task,
            read_only=False,
        )
    )

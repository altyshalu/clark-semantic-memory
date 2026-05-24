import json

from .memory import ClarkSemanticMemory, build_parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    memory = ClarkSemanticMemory(db_path=args.db)

    try:
        if args.command == "remember-project":
            print(json.dumps(
                memory.remember_project(args.statement, project=args.project, canonical_key=args.key),
                ensure_ascii=False,
            ))
        elif args.command == "remember-tool":
            print(json.dumps(
                memory.remember_tool(args.statement, tool_name=args.tool_name, canonical_key=args.key),
                ensure_ascii=False,
            ))
        elif args.command == "remember-rule":
            print(json.dumps(
                memory.remember_rule(args.statement, rule_name=args.rule_name, canonical_key=args.key),
                ensure_ascii=False,
            ))
        elif args.command == "query":
            print(json.dumps(memory.query(args.text, limit=args.limit), ensure_ascii=False, indent=2))
        elif args.command == "context":
            print(memory.session_context())
        elif args.command == "list":
            print(json.dumps(memory.list_entries(kind=args.kind), ensure_ascii=False, indent=2))
    finally:
        memory.close()

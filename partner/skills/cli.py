#!/usr/bin/env python3
"""
Partner Skill CLI - register, list, delete, sync skills.
Usage: python3 -m partner.skills.cli register --name <name> [options]
       python3 -m partner.skills.cli list [--category <cat>]
       python3 -m partner.skills.cli delete --name <name>
       python3 -m partner.skills.cli sync --instance <id>
"""
import argparse, json, sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

def cmd_register(args):
    from partner.skills.skill_center import register_skill
    ok = register_skill(
        name=args.name,
        version=args.version,
        description=args.description,
        command_template=args.command,
        parameters={"command": args.command} if args.command else {},
        allowed_agents=args.agents.split(",") if args.agents else ["hermes"],
        category=args.category or "general",
        dependencies=args.depends.split(",") if args.depends else [],
    )
    if ok:
        print(f"[OK] Skill '{args.name}' v{args.version} registered (category: {args.category})")
        return 0
    print(f"[FAIL] Could not register skill '{args.name}'")
    return 1

def cmd_list(args):
    from partner.skills.skill_center import list_skills
    skills = list_skills(category=args.category, enabled_only=not args.all)
    if not skills:
        print("No skills found.")
        return 0
    print(f"Found {len(skills)} skill(s):")
    print(f"{'NAME':<30} {'VERSION':<10} {'CATEGORY':<15} {'DEPENDENCIES':<20}")
    print("-" * 80)
    for s in skills:
        deps = json.loads(s.get("dependencies", "[]"))
        deps_str = ", ".join(str(d) for d in deps[:3]) if deps else "-"
        print(f"{s['name']:<30} {s['version']:<10} {s.get('category',''):<15} {deps_str:<20}")
    return 0

def cmd_delete(args):
    import sqlite3
    db_path = os.path.expanduser("~/.partner/skills_registry.db")
    if not os.path.exists(db_path):
        print(f"[FAIL] Database not found: {db_path}")
        return 1
    db = sqlite3.connect(db_path)
    cur = db.execute("DELETE FROM skills WHERE name=? AND version=?", (args.name, args.version or "1.0.0"))
    deleted = cur.rowcount
    db.commit()
    db.close()
    if deleted:
        print(f"[OK] Deleted {deleted} record(s) for '{args.name}'")
    else:
        print(f"[WARN] No record found for '{args.name}'")
    return 0

def cmd_sync(args):
    from partner.skills.skill_center import sync_skills_to_instance, get_instance_skills
    count = sync_skills_to_instance(args.instance)
    skills = get_instance_skills(args.instance)
    print(f"[OK] Synced {count} skills to instance {args.instance}")
    print(f"Instance {args.instance} now has {len(skills)} active skill(s)")
    for s in skills:
        print(f"  - {s['name']} v{s['version']} ({s.get('category','')})")
    return 0

def main():
    parser = argparse.ArgumentParser(prog="partner skill", description="Partner Skill Management")
    sub = parser.add_subparsers(dest="action", required=True)

    p_register = sub.add_parser("register", help="Register a new skill")
    p_register.add_argument("--name", required=True, help="Skill name")
    p_register.add_argument("--version", default="1.0.0", help="Semantic version (default: 1.0.0)")
    p_register.add_argument("--description", default="", help="Skill description")
    p_register.add_argument("--command", default="", help="Shell command template")
    p_register.add_argument("--category", default="general", help="Skill category")
    p_register.add_argument("--agents", default="hermes", help="Comma-separated allowed agents")
    p_register.add_argument("--depends", default="", help="Comma-separated dependency skill names")
    p_register.set_defaults(func=cmd_register)

    p_list = sub.add_parser("list", help="List registered skills")
    p_list.add_argument("--category", help="Filter by category")
    p_list.add_argument("--all", action="store_true", help="Include disabled skills")
    p_list.set_defaults(func=cmd_list)

    p_delete = sub.add_parser("delete", help="Delete a skill")
    p_delete.add_argument("--name", required=True, help="Skill name to delete")
    p_delete.add_argument("--version", default="", help="Version (default: all versions)")
    p_delete.set_defaults(func=cmd_delete)

    p_sync = sub.add_parser("sync", help="Sync skills to an instance")
    p_sync.add_argument("--instance", required=True, help="Instance ID (e.g. 01, 04)")
    p_sync.set_defaults(func=cmd_sync)

    args = parser.parse_args()
    return args.func(args)

if __name__ == "__main__":
    sys.exit(main())

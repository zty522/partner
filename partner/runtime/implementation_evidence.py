"""Bounded static evidence for semantic review; never executes candidate code."""
import ast
from pathlib import Path


def inspect_implementations(paths, limit=3):
    result=[]
    for raw in paths:
        path=Path(raw)
        if path.suffix!='.py' or not path.is_file() or path.stat().st_size>250_000:continue
        row={'path':str(path),'functions':[], 'calls':[], 'constant_collections':[]}
        try:
            tree=ast.parse(path.read_text(errors='replace'))
            row['functions']=[n.name for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))][:40]
            calls=set()
            for node in ast.walk(tree):
                if isinstance(node,ast.Call):calls.add(ast.unparse(node.func))
                if isinstance(node,ast.Assign) and isinstance(node.value,(ast.List,ast.Tuple)) and len(node.value.elts)>=5:
                    try: values=ast.literal_eval(node.value)
                    except (ValueError,TypeError):continue
                    row['constant_collections'].append({'name':ast.unparse(node.targets[0]),'count':len(values),
                        'examples':[str(v)[:120] for v in values[:2]],'line':node.lineno})
            row['calls']=sorted(calls)[:100]
        except (SyntaxError,ValueError) as exc:row['parse_error']=str(exc)
        result.append(row)
        if len(result)>=limit:break
    return result

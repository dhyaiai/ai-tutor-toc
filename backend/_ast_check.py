# 临时诊断脚本：定位批次循环内 commit/else 的缩进归属
import ast

src = open('app/tasks/analysis_tasks.py', encoding='utf-8').read()
lines = src.splitlines()
tree = ast.parse(src)


def indent_of(lineno):
    return len(lines[lineno - 1]) - len(lines[lineno - 1].lstrip())


for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == '_do_analyze_inner':
        print('FOUND _do_analyze_inner @%d' % node.lineno)
        for sub in ast.walk(node):
            if isinstance(sub, ast.While):
                print('=== While @%d (indent=%d) ===' % (sub.lineno, indent_of(sub.lineno)))
                for body_stmt in sub.body:
                    print('  while.body @%d indent=%d: %s' % (
                        body_stmt.lineno, indent_of(body_stmt.lineno), type(body_stmt).__name__))
                    if isinstance(body_stmt, ast.AsyncWith):
                        for item in body_stmt.body:
                            print('    asyncwith.body @%d indent=%d: %s' % (
                                item.lineno, indent_of(item.lineno), type(item).__name__))
                            if isinstance(item, ast.For):
                                print('      for @%d indent=%d orelse=%s' % (
                                    item.lineno, indent_of(item.lineno),
                                    [(o.lineno, type(o).__name__) for o in item.orelse]))
                                for o in item.orelse:
                                    print('        orelse stmt @%d indent=%d: %s' % (
                                        o.lineno, indent_of(o.lineno), type(o).__name__))
                # 输出 690-845 区间所有语句
                print('--- 区间 690-845 ---')
                for stmt in ast.walk(sub):
                    if not hasattr(stmt, 'lineno') or not (690 <= stmt.lineno <= 845):
                        continue
                    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Await) and \
                            hasattr(stmt.value.func, 'attr'):
                        desc = 'await ' + stmt.value.func.attr
                    elif isinstance(stmt, ast.Assign):
                        desc = 'assign ' + ','.join(
                            t.id if isinstance(t, ast.Name) else '?'
                            for t in stmt.targets)
                    elif isinstance(stmt, (ast.For, ast.If, ast.While, ast.AsyncWith, ast.Break, ast.Continue)):
                        desc = type(stmt).__name__ + (' else!' if isinstance(stmt, ast.If) and stmt.orelse else '')
                    else:
                        continue
                    print('  @%d indent=%d: %s' % (stmt.lineno, indent_of(stmt.lineno), desc[:60]))

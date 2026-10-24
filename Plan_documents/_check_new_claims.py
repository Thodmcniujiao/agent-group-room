# -*- coding: utf-8 -*-
"""核对新增方案中的两处可验证断言：
1) SUMMARY_RULE / BREVITY 的字符规模与方案里标注的 token 数是否吻合；
2) E7 的 pre_tool_hook 生成的 python -c 命令能否通过语法编译。
"""
import json

SUMMARY_RULE = ('如果你的回复超过 {min_chars} 字，在正文后单独写一行摘要，不超过 {max_chars} 字：\n'
                '摘要：立场=<支持/反对/有条件/中立>｜要点=<最重要的一两点>｜未决=<仍未解决的问题，没有写无>\n'
                '表态轮里，摘要行放在表态行之前，表态行仍是最后一行。')
BREVITY = ("发言只写结论、依据和需要别人做的事；"
           "不复述题目和别人的话；不写客套、不写总结性重复；"
           "代码与命令照常完整。")

rendered = SUMMARY_RULE.format(min_chars=400, max_chars=120)

print("== 1. 规则文本规模 ==")
print("SUMMARY_RULE 未渲染字符数:", len(SUMMARY_RULE))
print("SUMMARY_RULE 渲染后字符数:", len(rendered))
print("BREVITY 字符数:", len(BREVITY))
print("（中文按 1 token ≈ 1.2–1.7 字估算）")
for chars, name in ((len(rendered), 'SUMMARY_RULE'), (len(BREVITY), 'BREVITY')):
    lo, hi = chars / 1.7, chars / 1.2
    print(f"{name}: 约 {lo:.0f}–{hi:.0f} token")
print("方案表格里标注：S1 = 约 30 token/条，E5 = 约 40 token")

print()
print("== 2. E7 生成命令的语法检查 ==")
filepath = 'huge.log'
wrapped = (f'python -c "'
           f'import sys, os, re; p = \'{filepath}\'; '
           f's = os.path.getsize(p) if os.path.exists(p) else 0; '
           f'lines = open(p, errors=\'ignore\').readlines() if s < 2_000_000 else []; '
           f'errs = [l for l in lines if re.search(r\'(?i)error|fail|exception\', l)]; '
           f'print(\'\\n\'.join(lines[:30] + [f\\\'...[跳过 {{len(lines)-60}} 行]...\\\'] + lines[-30:] if len(lines)>60 else lines)); '
           f'print(f\\\'\\n[沙箱截获: 原文件 {{s}} 字节, 提取错误行 {{len(errs)}} 条]\\\')'
           f'"')
print("生成的控制台命令：")
print(wrapped[:400], "...")
print()

# 取出 python -c 的代码体做编译检查
body = wrapped[len('python -c "'):-1]
try:
    compile(body, '<e7>', 'exec')
    print("编译结果: 通过")
except SyntaxError as e:
    print("编译结果: SyntaxError ->", e.msg)
    print("出错位置:", repr(body[max(0, (e.offset or 0) - 40):(e.offset or 0) + 40]))

print()
print("== 3. 命令改写是否会吞掉管道/重定向 ==")
import re
DANGEROUS = r'\bcat\s+([^\s|>]+\.(log|txt|json|csv|out))\b'
samples = [
    'cat build.log | grep -i error',
    'cat a.txt b.txt > merged.txt',
    'cd dist && cat runtime.log | head -5',
]
for s in samples:
    m = re.search(DANGEROUS, s)
    print(f"  输入: {s!r:45} 命中: {bool(m)} -> 改写后丢失管道/重定向: {bool(m)}")

print()
print("== 4. git diff 改写对退出码的影响 ==")
print("  原命令 'git diff' 有差异时退出码 1；改写后 'git diff --stat && echo --- && git diff -U2 ...'")
print("  前半段 --stat 恒为 0，若后半段被截断，验收逻辑将无法区分“无差异”和“未执行”。")

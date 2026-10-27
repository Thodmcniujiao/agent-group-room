"""Contract-limited file tools for Codex, without granting native OS write access."""
import copy
from pathlib import Path
from deepseek_mcp.config import Config
from deepseek_mcp.tools import execute_tool
from deepseek_mcp.tool_schemas import TOOL_SCHEMAS
from deepseek_mcp.resource_budget import MutationBudget


class ProjectTools:
    names=['Read','Glob','Grep','Write','Edit']
    def __init__(self,root,allowed_paths,output_presenter=None):
        from windows_scripts import install
        install()
        self.root=Path(root).resolve();self.allowed=set(allowed_paths or [])
        self.config=Config('unused-no-network',self.root,allowed_tools=self.names)
        self.budget=MutationBudget();self.calls=0
        self.output_presenter=output_presenter

    @classmethod
    def specs(cls):
        from git_access import TOOL_SCHEMA
        return [{'type':'function','name':'project_'+name,'description':TOOL_SCHEMAS[name]['description']+
            ' Host enforces the current task allowed_paths; never use native apply_patch or shell to write.',
            'inputSchema':copy.deepcopy(TOOL_SCHEMAS[name]['parameters'])} for name in cls.names]+\
            [{'type':'function','name':'project_Git','description':TOOL_SCHEMA['description'],
              'inputSchema':copy.deepcopy(TOOL_SCHEMA['parameters'])}]

    def execute(self,name,args):
        tool=name.removeprefix('project_')
        if tool!='Git' and tool not in self.names:raise ValueError('文件工具不在允许列表内。')
        if not isinstance(args,dict):raise ValueError('文件工具参数必须是对象。')
        self.calls+=1
        if self.calls>128:raise ValueError('本轮文件工具调用达到上限。')
        if tool=='Git':
            from engines import preferences
            from git_access import ensure_config,run_readonly
            return run_readonly(self.root,args.get('args'),ensure_config(self.root,preferences.STATE),output_presenter=self.output_presenter)
        args=copy.deepcopy(args)
        if 'path' in args and isinstance(args['path'],str):
            target=(self.root/args['path']).resolve()
            if not target.is_relative_to(self.root):raise ValueError('路径越出当前项目。')
            relative=target.relative_to(self.root).as_posix();args['path']=relative
            if tool in ['Write','Edit'] and relative not in self.allowed:raise ValueError('此文件不在本轮执行契约允许修改范围内。')
            if any(p.lower() in ['.git','.codex','.claude','credentials','.deepseek-mcp'] or p.lower().startswith('.env') for p in target.relative_to(self.root).parts):
                raise ValueError('不能通过文件工具访问凭据或控制目录。')
        elif tool in ['Write','Edit']:raise ValueError('缺少有效的文件路径。')
        if tool=='Read':args['limit']=min(400,max(1,int(args.get('limit',200))))
        return execute_tool(tool,args,self.config,mutation_budget=self.budget)

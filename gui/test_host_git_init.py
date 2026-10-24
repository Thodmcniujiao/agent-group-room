"""Host-owned repository preparation with real temporary Git repositories."""
import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock,patch

import git_access
from preflight import git_check


class HostGitInitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='agent-room-git-init-')
        self.base=Path(self.temp.name);self.root=self.base/'project';self.root.mkdir()
        self.state=self.base/'state'

    async def asyncTearDown(self):self.temp.cleanup()

    async def git(self,*args,root=None):
        return await git_access.run_host(root or self.root,*args,state_dir=self.state)

    async def prepare(self,root=None):
        return await git_access.ensure_repository(root or self.root,state_dir=self.state)

    async def test_empty_folder_initializes_real_repo_without_commit_or_remote(self):
        with patch('preflight.preferences.STATE',self.state):
            row=await git_check(self.root)
            executor=await git_check(self.root,True)
        self.assertTrue(row['ok'],row);self.assertTrue(row['initialized'])
        self.assertTrue(executor['ok'],executor)
        self.assertIn('宿主已自动执行 git init',row['detail'])
        self.assertEqual(Path((await self.git('rev-parse','--show-toplevel')).strip()).resolve(),self.root.resolve())
        self.assertEqual((await self.git('status','--short')).strip(),'')
        self.assertEqual((await self.git('remote')).strip(),'')
        self.assertEqual(sorted(p.name for p in self.root.iterdir()),['.git'])
        self.assertFalse((self.root/'.git/index').exists())
        self.assertEqual(list((self.root/'.git/refs/heads').iterdir()),[])

    async def test_nonempty_folder_preserves_every_original_byte_and_global_config(self):
        (self.root/'nested').mkdir()
        files={'hello.txt':b'original\x00\xff','nested/data.bin':bytes(range(256))}
        for name,data in files.items():(self.root/name).write_bytes(data)
        user=self.base/'user.gitconfig';template=self.base/'template';template.mkdir()
        (template/'marker').write_text('must not be copied',encoding='utf-8')
        config='[init]\n\ttemplateDir = '+template.as_posix()+'\n'
        user.write_text(config,encoding='utf-8')
        with patch.dict('os.environ',{'TIERED_USER_GIT_CONFIG_GLOBAL':str(user)}):
            self.assertTrue((await self.prepare())['initialized'])
        for name,data in files.items():self.assertEqual((self.root/name).read_bytes(),data)
        self.assertEqual(user.read_text(encoding='utf-8'),config)
        self.assertFalse((self.root/'.git/marker').exists())
        self.assertEqual((await self.git('ls-files')).strip(),'')

    async def test_existing_repository_is_not_reinitialized(self):
        await self.git('init','--quiet')
        await self.git('config','local.fixture','keep')
        before={p.relative_to(self.root/'.git').as_posix():p.read_bytes()
                for p in (self.root/'.git').rglob('*') if p.is_file()}
        self.assertFalse((await self.prepare())['initialized'])
        after={p.relative_to(self.root/'.git').as_posix():p.read_bytes()
               for p in (self.root/'.git').rglob('*') if p.is_file()}
        self.assertEqual(after,before)

    async def test_parent_repository_does_not_get_a_nested_repo(self):
        await self.git('init','--quiet')
        child=self.root/'child';child.mkdir()
        with patch('preflight.preferences.STATE',self.state):row=await git_check(child)
        self.assertFalse(row['ok']);self.assertIn('实际根目录',row['detail'])
        self.assertFalse((child/'.git').exists())

    async def test_worktree_git_file_is_preserved(self):
        await self.git('init','--quiet')
        (self.root/'tracked.txt').write_text('fixture',encoding='utf-8')
        await self.git('add','tracked.txt')
        await self.git('-c','user.name=Test','-c','user.email=test@example.invalid','commit','-qm','fixture')
        tree=self.base/'worktree'
        await self.git('worktree','add','-qb','second',str(tree))
        marker=(tree/'.git').read_bytes()
        self.assertFalse((await self.prepare(tree))['initialized'])
        self.assertEqual((tree/'.git').read_bytes(),marker)

    async def test_invalid_local_or_parent_git_metadata_is_never_overwritten(self):
        for kind in ['directory','file','parent']:
            with self.subTest(kind=kind):
                folder=self.base/kind;folder.mkdir()
                selected=folder
                if kind=='file':(folder/'.git').write_text('gitdir: missing-location',encoding='utf-8')
                else:(folder/'.git').mkdir()
                if kind=='parent':selected=folder/'child';selected.mkdir()
                with self.assertRaises(git_access.GitError):await self.prepare(selected)
                if kind=='file':self.assertEqual((folder/'.git').read_text(),'gitdir: missing-location')
                else:self.assertEqual(list((folder/'.git').iterdir()),[])
                if kind=='parent':self.assertFalse((selected/'.git').exists())

    async def test_bare_repository_is_not_converted(self):
        await self.git('init','--bare','--quiet')
        with self.assertRaises(git_access.GitError):await self.prepare()
        self.assertFalse((self.root/'.git').exists())
        self.assertEqual((await self.git('rev-parse','--is-bare-repository')).strip(),'true')

    async def test_concurrent_preflights_initialize_only_once(self):
        rows=await asyncio.gather(*(self.prepare() for _ in range(5)))
        self.assertEqual(sum(r['initialized'] for r in rows),1)
        self.assertTrue(all(Path(r['root']).resolve()==self.root.resolve() for r in rows))

    async def test_non_missing_repository_errors_never_invoke_init(self):
        for error in [git_access.GitError('git missing'),
                      git_access.GitError('denied',128,'fatal: Permission denied'),
                      git_access.GitError('unsafe',128,'fatal: detected dubious ownership'),
                      PermissionError('metadata inaccessible')]:
            with self.subTest(error=str(error)):
                call=AsyncMock(side_effect=error)
                with patch('git_access.run_host',call):
                    with self.assertRaises(type(error)):await self.prepare()
                self.assertEqual(call.await_count,1)
                self.assertFalse((self.root/'.git').exists())

    async def test_init_failure_preserves_actual_error_without_retry_or_cleanup(self):
        missing=git_access.GitError('missing',128,'fatal: not a git repository (or any of the parent directories): .git')
        denied=git_access.GitError('cannot mkdir',128,'fatal: cannot mkdir .git: Permission denied')
        call=AsyncMock(side_effect=[missing,denied])
        with patch('git_access.run_host',call):
            with self.assertRaises(git_access.GitError) as caught:await self.prepare()
        self.assertIs(caught.exception,denied);self.assertEqual(call.await_count,2)
        self.assertIn('init',call.await_args.args)

    async def test_metadata_permission_failure_does_not_invoke_init(self):
        missing=git_access.GitError('missing',128,'fatal: not a git repository (or any of the parent directories): .git')
        call=AsyncMock(side_effect=missing)
        with patch('git_access.run_host',call),patch('git_access._assert_no_repository_metadata',side_effect=PermissionError('access denied')):
            with self.assertRaises(PermissionError):await self.prepare()
        self.assertEqual(call.await_count,1)

    async def test_inherited_git_redirection_cannot_initialize_another_folder(self):
        outside=self.base/'outside';outside.mkdir()
        with patch.dict('os.environ',{'GIT_DIR':str(outside/'wrong.git'),'GIT_WORK_TREE':str(outside)}):
            self.assertTrue((await self.prepare())['initialized'])
        self.assertEqual(list(outside.iterdir()),[])
        self.assertTrue((self.root/'.git').is_dir())

    async def test_home_and_runtime_folders_are_rejected_before_git_call(self):
        call=AsyncMock()
        with patch('git_access.run_host',call):
            for root in [Path.home(),Path.home()/'.codex',Path.home()/'.local']:
                with self.assertRaises(ValueError):await self.prepare(root)
        call.assert_not_awaited()

    def test_model_git_tool_still_cannot_initialize(self):
        with self.assertRaises(ValueError):git_access.validate_args(['init'])


if __name__=='__main__':unittest.main()

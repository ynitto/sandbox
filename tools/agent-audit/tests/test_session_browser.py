import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from agent_audit import session_browser as browser

class BrowserTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
    def write(self, relative, objects, lines=True):
        p = self.root / relative
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('\n'.join(json.dumps(o) for o in objects) if lines else json.dumps(objects))
        return p
    def vscode(self):
        return {'sessionId': 'vs-1', 'creationDate': 1000000000000, 'workingDirectory': 'file:///C:/work/report',
                'requests': [{'requestId': 'r1', 'timestamp': 1000000000001, 'message': {'text': '集計'},
                              'modelId': 'model-a', 'response': [{'value': '完了'}], 'modelState': {'value': 1}}]}
    def test_inventory_and_page_do_not_read_unrequested_bodies(self):
        for i in range(80):
            self.write(f'.vscode-server/data/User/workspaceStorage/w/chatSessions/{i}.json', self.vscode(), False)
        with patch.object(browser, 'read_file', side_effect=AssertionError('inventory must not read bodies')):
            found = browser.inventory({'home': str(self.root), 'query': {'source': 'vscode'}})
        self.assertEqual(len(found['descriptors']), 80)
        with patch.object(browser, 'read_file', wraps=browser.read_file) as read:
            result = browser.scan({'descriptors': found['descriptors'][:5]})
        self.assertEqual(len(result['sessions']), 5)
        self.assertEqual(read.call_count, 5)

    def test_large_vscode_mutation_log_is_streamed(self):
        p = self.root / 'large.jsonl'
        with p.open('w') as f:
            f.write(json.dumps({'kind': 0, 'v': self.vscode()}) + '\n')
            update = json.dumps({'kind': 1, 'k': ['requests', 0, 'response', 0, 'value'], 'v': 'x' * 1048576}) + '\n'
            for _ in range(65):
                f.write(update)
            f.write(json.dumps({'kind': 1, 'k': ['requests', 0, 'response', 0, 'value'], 'v': '最終回答'}) + '\n')
        with patch.object(Path, 'read_text', side_effect=AssertionError('must stream')):
            r = browser.read_file({'path': str(p), 'provider': 'vscode'})
        self.assertEqual([m['text'] for m in r['messages']], ['集計', '最終回答'])
        self.assertFalse(r['partial'])

    def test_current_kiro_database_discovery_and_history(self):
        for relative in ['Library/Application Support/kiro-cli/data.sqlite3', '.local/share/kiro-cli/data.sqlite3']:
            p = self.root / relative
            p.parent.mkdir(parents=True, exist_ok=True)
            payload = {'conversation_id': 'kiro-current', 'model_info': {'model_id': 'model-k'},
                       'history': [
                           {'user': {'content': {'Prompt': {'prompt': '依頼'}}, 'env_context': {'env_state': {'current_working_directory': '/work'}}},
                            'assistant': {'ToolUse': {'content': '調査中', 'tool_uses': [{'secret': 'omit'}]}}},
                           {'user': {'content': {'ToolUseResults': {'tool_use_results': ['omit']}}},
                            'assistant': {'Response': {'content': '回答', 'thinking': {'text': 'omit'}}}}]}
            with sqlite3.connect(p) as c:
                c.execute('CREATE TABLE conversations (key TEXT, value TEXT)')
                c.execute('CREATE TABLE conversations_v2 (key TEXT, conversation_id TEXT, value TEXT, created_at INTEGER, updated_at INTEGER)')
                c.execute('INSERT INTO conversations_v2 VALUES (?, ?, ?, ?, ?)', ('/work', 'kiro-current', json.dumps(payload), 1700000000000, 1700000001000))
            c.close()
            found = list(browser.discover({'home': str(self.root)}))
            entry = next(d for d in found if d['path'] == str(p))
            r = browser.read_file(entry)
            self.assertEqual(r['nativeId'], 'kiro-current')
            self.assertEqual(r['repo'], '/work')
            self.assertEqual(r['model'], 'model-k')
            self.assertEqual([m['text'] for m in r['messages']], ['依頼', '調査中', '回答'])
            self.assertFalse(r['messages'][1]['complete'])
            self.assertTrue(r['messages'][2]['complete'])
            self.assertEqual(r['updatedAt'], 1700000001)

    def test_vscode_json_and_mutations_preserve_replaced_response(self):
        obj = self.vscode()
        log = [{'kind': 0, 'v': obj}, {'kind': 1, 'k': ['requests', 0, 'response', 0, 'value'], 'v': '訂正後'},
               {'kind': 2, 'k': ['requests'], 'v': [{'requestId': 'r2', 'message': {'text': '次'}, 'response': [{'value': '途中'}], 'modelState': {'value': 0}}]},
               {'kind': 2, 'k': ['requests'], 'i': 1}]
        p = self.write('chat.jsonl', log)
        r = browser.read_file({'path': str(p), 'provider': 'vscode'})
        self.assertEqual([m['text'] for m in r['messages']], ['集計', '訂正後'])
        self.assertEqual(r['repo'], 'C:/work/report')
        self.assertTrue(r['messages'][-1]['complete'])
        p = self.write('export.json', obj, False)
        self.assertEqual(browser.read_file({'path': str(p), 'provider': 'vscode'})['nativeId'], 'vs-1')
    def test_corrupt_log_is_partial_and_unknown_operation_rejected(self):
        p = self.write('chat.jsonl', [{'kind': 0, 'v': self.vscode()}])
        p.write_text(p.read_text() + '\n{"unfinished":')
        self.assertTrue(browser.read_file({'path': str(p), 'provider': 'vscode'})['partial'])
        with self.assertRaises(ValueError):
            browser.restore_vscode([{'kind': 0, 'v': self.vscode()}, {'kind': 100, 'k': []}])
    def test_copilot_event_completion_and_subagents(self):
        events = [{'type': 'session.start', 'data': {'sessionId': 'cp1', 'context': {'cwd': '/work'}, 'selectedModel': 'model-x'}},
                  {'type': 'user.message', 'id': 'u', 'data': {'content': '依頼'}},
                  {'type': 'assistant.message', 'id': 'a', 'data': {'content': '結果'}},
                  {'type': 'assistant.turn_end', 'data': {}},
                  {'type': 'assistant.message', 'agentId': 'child', 'data': {'content': '混ぜない'}},
                  {'type': 'assistant.message', 'id': 'b', 'data': {'content': '応答中'}}]
        p = self.write('.copilot/session-state/cp1/events.jsonl', events)
        r = browser.read_file({'path': str(p), 'provider': 'copilot'})
        self.assertEqual(r['nativeId'], 'cp1')
        self.assertEqual(r['model'], 'model-x')
        self.assertEqual(len(r['messages']), 3)
        self.assertTrue(r['messages'][1]['complete'])
        self.assertFalse(r['messages'][2]['complete'])
    def test_codex_full_text_and_nested_identity(self):
        body = '全文' * 9000
        p = self.write('.codex/sessions/rollout.jsonl', [
            {'type': 'session_meta', 'payload': {'id': 'native-id', 'cwd': '/work'}},
            {'type': 'response_item', 'payload': {'role': 'user', 'content': [{'type': 'input_text', 'text': body}]}},
            {'type': 'response_item', 'payload': {'role': 'assistant', 'content': [{'type': 'output_text', 'text': '完了'}]}}])
        r = browser.read_file({'path': str(p), 'provider': 'codex'})
        self.assertEqual(r['nativeId'], 'native-id')
        self.assertEqual(r['messages'][0]['text'], body)
    def test_discovery_crosses_homes_and_profiles_and_filters(self):
        self.write('Library/Application Support/Code/User/profiles/p/workspaceStorage/w/chatSessions/v.json', self.vscode(), False)
        other = self.root / 'windows'
        self.write('windows/.claude/projects/work/c.jsonl', [{'sessionId': 'c', 'cwd': '/other', 'message': {'role': 'user', 'content': '検索語'}}])
        with patch.dict(os.environ, {'CODEX_HOME': '', 'CLAUDE_CONFIG_DIR': ''}):
            result = browser.scan({'home': str(self.root), 'extraHomes': [str(other)], 'query': {'text': '検索語'}})
        self.assertEqual(len(result['sessions']), 1)
        self.assertEqual(result['sessions'][0]['agent'], 'claude')
        self.assertNotIn('messages', result['sessions'][0])
    def test_windows_and_mounted_paths_match_without_a_visible_environment_filter(self):
        self.assertEqual(browser.comparable_path("C:\\Users\\Test\\repo"), browser.comparable_path("/mnt/c/Users/Test/repo"))

    def test_kiro_read_only_database(self):
        db = self.root / 'store.db'
        conn = sqlite3.connect(db)
        conn.execute('create table sessions(id text, messages text, directory text)')
        conn.execute('insert into sessions values(?,?,?)', ('k1', json.dumps([{'role': 'user', 'content': 'test'}]), '/work'))
        conn.commit(); conn.close()
        original = db.read_bytes()
        record = browser.read_file({'path': str(db), 'provider': 'kiro', 'nativeId': 'k1'})
        self.assertEqual(record['messages'][0]['text'], 'test')
        self.assertEqual(db.read_bytes(), original)
    def test_claude_active_branch_does_not_include_abandoned_messages(self):
        objects = [
            {"uuid": "u", "parentUuid": None, "message": {"role": "user", "content": "依頼"}},
            {"uuid": "a", "parentUuid": "u", "message": {"role": "assistant", "content": "以前の分岐"}},
            {"uuid": "b", "parentUuid": "u", "message": {"role": "assistant", "content": "採用した分岐"}},
        ]
        self.assertEqual([m["text"] for m in browser.cli_messages(objects, "claude")], ["依頼", "採用した分岐"])

    def test_pending_vscode_response_is_not_forkable(self):
        obj = self.vscode(); obj['requests'][0]['modelState'] = {'value': 0}
        p = self.write('pending.json', obj, False)
        self.assertFalse(browser.read_file({'path': str(p), 'provider': 'vscode'})['messages'][-1]['complete'])

if __name__ == '__main__':
    unittest.main()

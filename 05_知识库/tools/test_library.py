import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from library import Library, rebuild, safe_path, stable_id

ROOT=Path(sys.argv[1]) if len(sys.argv)>1 else Path(__file__).resolve().parents[2]
sys.argv=[sys.argv[0]]

class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib=Library(ROOT)
        source=json.loads((ROOT/"工作流详细索引.json").read_text("utf-8"))
        extra=ROOT/"06_自建工作流/entries.json"
        cls.expected=len(source["workflows"])+(len(json.loads(extra.read_text("utf-8"))) if extra.exists() else 0)

    def test_all_files_hash_and_parse(self):
        result=self.lib.verify()
        self.assertTrue(result["ok"],result["errors"])
        self.assertEqual(result["checked"],self.expected)

    def test_stable_ids_and_legacy_aliases(self):
        self.assertEqual(len(self.lib.by_id),self.expected)
        c=self.lib.cards[0]
        self.assertEqual(self.lib.get(c["legacy_id"])["uid"],c["uid"])
        self.assertEqual(stable_id(c["source"]["repo"],c["source"]["source_path"]),c["uid"])

    def test_design_queries(self):
        examples={"展会海报扩图留白":"outpaint","产品换背景":"product_background","给摩托车打光":"relight","产品图放大高清":"upscale","产品短视频":"video"}
        for q,task in examples.items():
            with self.subTest(q=q):
                out=self.lib.recommend(q,runtime="local")
                self.assertGreater(out["count"],0)
                c=self.lib.get(out["results"][0]["uid"])
                self.assertIn(task,c["tasks"])
                self.assertFalse(c["readiness"]["gpu_run_tested"])

    def test_exact_texture_is_not_promised(self):
        out=self.lib.recommend("把锻造碳纤维纹理一模一样贴到整流罩")
        self.assertEqual(out["decision"],"no_verified_exact_texture_transfer")
        self.assertIn("supporting_workflows_only",out)

    def test_cloud_filter_and_unknown_query(self):
        self.assertTrue(all(c["runtime"]=="cloud_or_hybrid" for c in self.lib.search(runtime="cloud")["results"]))
        self.assertEqual(self.lib.search("no_such_model_9283847")["count"],0)

    def test_model_registry_and_db(self):
        self.assertGreater(len(self.lib.model_usage("iclight")),0)
        db=sqlite3.connect(ROOT/"05_知识库/library.sqlite3")
        self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0],"ok")
        self.assertEqual(db.execute("SELECT count(*) FROM workflows").fetchone()[0],self.expected)
        db.close()

    def test_safe_paths(self):
        with self.assertRaises(ValueError):safe_path(ROOT,"../not-in-library.json")

    def test_mcp_stdio_contract(self):
        packets=[
            {"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"test","version":"1"}}},
            {"jsonrpc":"2.0","method":"notifications/initialized"},
            {"jsonrpc":"2.0","id":2,"method":"tools/list"},
            {"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"search_workflows","arguments":{"task":"outpaint","limit":2}}},
            {"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"get_workflow","arguments":{"uid":"not_a_workflow"}}},
            {"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"search_workflows","arguments":{"limit":False}}},
            {"jsonrpc":"2.0","id":6,"method":"arbitrary/execute","params":{}},
        ]
        p=subprocess.run([sys.executable,str(Path(__file__).with_name("mcp_server.py")),str(ROOT)],input="\n".join(json.dumps(x) for x in packets)+"\n",capture_output=True,text=True,encoding="utf-8",timeout=30)
        self.assertEqual(p.returncode,0,p.stderr)
        replies=[json.loads(line) for line in p.stdout.splitlines()]
        self.assertEqual(len(replies),6)
        self.assertEqual(len(replies[1]["result"]["tools"]),6)
        self.assertFalse(replies[2]["result"]["isError"])
        self.assertTrue(replies[3]["result"]["isError"])
        self.assertTrue(replies[4]["result"]["isError"])
        self.assertEqual(replies[5]["error"]["code"],-32601)

    def test_rebuild_preserves_user_notes_and_custom_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            row=json.loads((ROOT/"工作流详细索引.json").read_text("utf-8"))["workflows"][0]
            destination=root/row["path"];destination.parent.mkdir(parents=True);shutil.copyfile(ROOT/row["path"],destination)
            (root/"工作流详细索引.json").write_text(json.dumps({"workflows":[row]}),"utf-8")
            rebuild(root);lib=Library(root);uid=lib.cards[0]["uid"]
            lib.annotate(uid,True,"已收藏，后续测试")
            rebuild(root)
            self.assertEqual(Library(root).get(uid)["user_annotation"]["note"],"已收藏，后续测试")
            self.assertEqual(Library(root).cards[0]["uid"],uid)
            fixture=root/"fixture.json";fixture.write_text(json.dumps({"nodes":[{"id":1,"type":"LoadImage","widgets_values":["own.png"]}]}),"utf-8")
            result=lib.import_file(fixture,name="我的测试流程",task_ids=["matting"],parent_uid=uid)
            self.assertEqual(len(Library(root).cards),2)
            card=Library(root).get(result["uid"])
            self.assertEqual(card["parent_uid"],uid)
            self.assertEqual(card["runtime"],"unknown")
            self.assertEqual(Library(root).search(runtime="local")["count"],1)
            bad=root/"bad.json";bad.write_text('{"not":"workflow"}',"utf-8")
            with self.assertRaises(ValueError):Library(root).import_file(bad)

if __name__=="__main__":
    unittest.main(verbosity=2)

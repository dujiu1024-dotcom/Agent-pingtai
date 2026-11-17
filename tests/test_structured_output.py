import unittest
from agent_platform.runtime.structured_output import *
class StructuredOutputTests(unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  self.r=OutputSchemaRegistry(); self.r.register("answer",1,{"type":"object","required":["answer","score"],"properties":{"answer":{"type":"string","minLength":1},"score":{"type":"number","minimum":0,"maximum":1}},"additionalProperties":False})
 async def test_valid_latest_version(self):
  self.r.register("answer",2,{"type":"string"}); x=await validate_structured_output('"ok"',registry=self.r,schema_id="answer"); self.assertEqual(x.schema_version,2)
 async def test_immutable_version_and_unknown_keyword(self):
  with self.assertRaises(SchemaVersionConflict): self.r.register("answer",1,{"type":"string"})
  with self.assertRaises(SchemaRegistrationError): self.r.register("bad",1,{"magic":True})
 async def test_validation_failure(self):
  with self.assertRaises(OutputValidationError): await validate_structured_output('{"answer":"","score":2}',registry=self.r,schema_id="answer")
 async def test_parse_failure(self):
  with self.assertRaises(OutputParseError): await validate_structured_output("{",registry=self.r,schema_id="answer")
 async def test_bounded_repair(self):
  calls=[]
  async def repair(raw,schema,issues,n): calls.append(n); return '{"answer":"fixed","score":0.5}'
  x=await validate_structured_output("{}",registry=self.r,schema_id="answer",max_repairs=2,repairer=repair); self.assertEqual(x.repairs,1); self.assertEqual(calls,[1])
 async def test_exhausted_repair(self):
  async def repair(raw,schema,issues,n): return "{}"
  with self.assertRaises(OutputValidationError) as e: await validate_structured_output("{}",registry=self.r,schema_id="answer",max_repairs=1,repairer=repair)
  self.assertEqual(e.exception.attempts,2)
 async def test_repair_limit(self):
  with self.assertRaises(ValueError): await validate_structured_output("{}",registry=self.r,schema_id="answer",max_repairs=6)
if __name__=="__main__": unittest.main()

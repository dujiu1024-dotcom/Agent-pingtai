"""Versioned JSON structured-output schemas with bounded repair retries."""
from __future__ import annotations
import inspect, json, math, re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping
from uuid import uuid4

SUPPORTED={"$schema","$id","title","description","type","properties","required","additionalProperties","items","enum","const","minimum","maximum","minLength","maxLength","pattern","minItems","maxItems","minProperties","maxProperties"}

class StructuredOutputError(RuntimeError):
    code="structured_output_error"
    def __init__(self,message,*,attempts=0,issues=()):
        super().__init__(message); self.attempts=attempts; self.issues=tuple(issues)
class SchemaRegistrationError(StructuredOutputError): code="schema_registration_error"
class SchemaNotFoundError(StructuredOutputError): code="schema_not_found"
class SchemaVersionConflict(StructuredOutputError): code="schema_version_conflict"
class OutputParseError(StructuredOutputError): code="output_parse_error"
class OutputValidationError(StructuredOutputError): code="output_validation_error"
class OutputRepairExhausted(StructuredOutputError): code="output_repair_exhausted"

@dataclass(frozen=True)
class OutputSchema:
    schema_id:str
    version:int
    definition:Mapping[str,Any]
    schema_ref:str=field(default_factory=lambda:str(uuid4()))
    def __post_init__(self):
        if not self.schema_id.strip() or self.version<1: raise ValueError("schema_id and positive version required")
@dataclass(frozen=True)
class StructuredOutputResult:
    value:Any; schema_id:str; schema_version:int; attempts:int; repairs:int; issues_before_repair:tuple[str,...]=()

def _check(s,path="$"):
    if not isinstance(s,dict): raise SchemaRegistrationError(f"{path} must be an object")
    unknown=set(s)-SUPPORTED
    if unknown: raise SchemaRegistrationError(f"unsupported keywords at {path}: {sorted(unknown)}")
    if s.get("type") not in (None,"object","array","string","number","integer","boolean","null"): raise SchemaRegistrationError(f"unsupported type at {path}")
    if "properties" in s:
        if not isinstance(s["properties"],dict): raise SchemaRegistrationError(f"{path}.properties must be object")
        for k,v in s["properties"].items(): _check(v,f"{path}.properties.{k}")
    if "items" in s: _check(s["items"],path+".items")
    if not isinstance(s.get("required",[]),list) or not all(isinstance(x,str) for x in s.get("required",[])): raise SchemaRegistrationError(f"{path}.required must be string array")
    for k in ("minimum","maximum"):
        if k in s and (isinstance(s[k],bool) or not isinstance(s[k],(int,float))): raise SchemaRegistrationError(f"{path}.{k} must be numeric")

def _validate(v,s,path="$"):
    out=[]; t=s.get("type")
    checks={"object":lambda x:isinstance(x,dict),"array":lambda x:isinstance(x,list),"string":lambda x:isinstance(x,str),"number":lambda x:isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x),"integer":lambda x:isinstance(x,int) and not isinstance(x,bool),"boolean":lambda x:isinstance(x,bool),"null":lambda x:x is None}
    if t and not checks[t](v): return [f"{path}: expected {t}"]
    if "enum" in s and v not in s["enum"]: out.append(f"{path}: value not in enum")
    if "const" in s and v!=s["const"]: out.append(f"{path}: value does not match const")
    if t=="object":
        props=s.get("properties",{})
        for k in s.get("required",[]):
            if k not in v: out.append(f"{path}.{k}: required property missing")
        if s.get("additionalProperties") is False:
            for k in v.keys()-props.keys(): out.append(f"{path}.{k}: additional property forbidden")
        for k,sub in props.items():
            if k in v: out.extend(_validate(v[k],sub,f"{path}.{k}"))
        for key,fn in (("minProperties",lambda n:len(v)<n),("maxProperties",lambda n:len(v)>n)):
            if key in s and fn(s[key]): out.append(f"{path}: violates {key}")
    if t=="array":
        for i,x in enumerate(v): out.extend(_validate(x,s.get("items",{}),f"{path}[{i}]"))
        for key,fn in (("minItems",lambda n:len(v)<n),("maxItems",lambda n:len(v)>n)):
            if key in s and fn(s[key]): out.append(f"{path}: violates {key}")
    if t=="string":
        if "minLength" in s and len(v)<s["minLength"]: out.append(f"{path}: too short")
        if "maxLength" in s and len(v)>s["maxLength"]: out.append(f"{path}: too long")
        if "pattern" in s and re.search(s["pattern"],v) is None: out.append(f"{path}: pattern mismatch")
    if t in ("number","integer"):
        if "minimum" in s and v<s["minimum"]: out.append(f"{path}: below minimum")
        if "maximum" in s and v>s["maximum"]: out.append(f"{path}: above maximum")
    return out

class OutputSchemaRegistry:
    def __init__(self): self._schemas={}
    def register(self,schema_id,version,definition):
        key=(schema_id,version)
        if key in self._schemas:
            if dict(self._schemas[key].definition)!=dict(definition): raise SchemaVersionConflict(f"{schema_id}@{version} immutable")
            return self._schemas[key]
        _check(dict(definition)); obj=OutputSchema(schema_id,version,dict(definition)); self._schemas[key]=obj; return obj
    def get(self,schema_id,version=None):
        versions=[v for sid,v in self._schemas if sid==schema_id]
        if not versions: raise SchemaNotFoundError(f"schema not found: {schema_id}")
        version=max(versions) if version is None else version
        try: return self._schemas[(schema_id,version)]
        except KeyError: raise SchemaNotFoundError(f"schema not found: {schema_id}@{version}")
    def validate(self,schema,value): return tuple(_validate(value,schema.definition))

async def validate_structured_output(raw_output,*,registry,schema_id,schema_version=None,max_repairs=1,repairer=None):
    if isinstance(max_repairs,bool) or not isinstance(max_repairs,int) or not 0<=max_repairs<=5: raise ValueError("max_repairs must be 0..5")
    schema=registry.get(schema_id,schema_version); current=raw_output; first=()
    for attempt in range(max_repairs+1):
        try: value=json.loads(current)
        except (TypeError,json.JSONDecodeError) as e:
            issues=(f"$: invalid JSON ({getattr(e,'msg','output must be text')})",)
            if attempt==0: first=issues
            if attempt>=max_repairs or repairer is None: raise OutputParseError("invalid JSON output",attempts=attempt+1,issues=issues) from e
        else:
            issues=registry.validate(schema,value)
            if not issues: return StructuredOutputResult(value,schema.schema_id,schema.version,attempt+1,attempt,first)
            if attempt==0: first=issues
            if attempt>=max_repairs or repairer is None: raise OutputValidationError("schema validation failed",attempts=attempt+1,issues=issues)
        fixed=repairer(current,schema,issues,attempt+1)
        current=await fixed if inspect.isawaitable(fixed) else fixed
        if not isinstance(current,str): raise OutputRepairExhausted("repairer must return text",attempts=attempt+1,issues=issues)
    raise OutputRepairExhausted("repair budget exhausted",attempts=max_repairs+1,issues=first)

from __future__ import annotations
import unittest
from fastapi import HTTPException
from agent_platform.api.idempotency import ApiIdempotencyService, IdempotencyClaim, IdempotencyResponse

class Backend:
    def __init__(self): self.rows = {}
    async def claim(self, **kw):
        scope = tuple(kw[k] for k in ('tenant_id','principal_id','method','route','key'))
        row = self.rows.get(scope)
        if row is None:
            self.rows[scope] = {'hash': kw['request_hash'], 'owner': kw['owner_token']}
            return IdempotencyClaim('owner', owner_token=kw['owner_token'])
        if row['hash'] != kw['request_hash']: return IdempotencyClaim('conflict')
        if 'response' in row: return IdempotencyClaim('replay', response=row['response'])
        return IdempotencyClaim('in_progress')
    async def complete(self, **kw):
        scope = tuple(kw[k] for k in ('tenant_id','principal_id','method','route','key'))
        row = self.rows[scope]
        if row.get('owner') != kw['owner_token']: return False
        row['response'] = kw['response']; row.pop('owner'); return True
    async def release(self, **kw):
        scope = tuple(kw[k] for k in ('tenant_id','principal_id','method','route','key'))
        row = self.rows.get(scope)
        if row is None or row.get('owner') != kw['owner_token']:
            return False
        self.rows.pop(scope, None)
        return True

class ApiIdempotencyServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_replays_response_and_does_not_repeat_side_effect(self):
        service = ApiIdempotencyService(Backend()); calls = []
        async def operation():
            calls.append(1)
            return IdempotencyResponse(201, {'content-type':'application/json'}, b'{"id":1}')
        args = dict(tenant_id='t1', principal_id='u1', method='POST', route='/runs', key='k1', body=b'{"x":1}', operation=operation)
        first, replay1 = await service.execute(**args)
        second, replay2 = await service.execute(**args)
        self.assertFalse(replay1); self.assertTrue(replay2)
        self.assertEqual(first, second); self.assertEqual(calls, [1])
    async def test_uncertain_operation_failure_keeps_claim_until_lease_recovery(self):
        backend = Backend()
        service = ApiIdempotencyService(backend)
        calls = []

        async def operation():
            calls.append(1)
            raise RuntimeError("downstream outcome is unknown")

        args = dict(
            tenant_id="tenant-a", principal_id="user-a", method="POST",
            route="/runs", key="retry-1", body=b'{"run":1}', operation=operation,
        )
        with self.assertRaisesRegex(RuntimeError, "outcome is unknown"):
            await service.execute(**args)
        with self.assertRaises(HTTPException) as busy:
            await service.execute(**args)
        self.assertEqual(busy.exception.detail["code"], "IDEMPOTENCY_REQUEST_IN_PROGRESS")
        self.assertEqual(calls, [1])

    async def test_conflicting_payload_and_in_progress_request_are_rejected(self):
        from agent_platform.api.idempotency import request_fingerprint
        backend = Backend(); service = ApiIdempotencyService(backend)
        operation = lambda: IdempotencyResponse(200, {}, b'ok')
        args = dict(tenant_id='t1', principal_id='u1', method='POST', route='/runs', key='k1', body=b'one', operation=operation)
        await service.execute(**args)
        with self.assertRaises(HTTPException) as caught:
            await service.execute(**{**args, 'body':b'two'})
        self.assertEqual(caught.exception.status_code, 409)
        # Seed an in-progress claim for a different principal: same scope,
        # same request_hash (body=b'one'), so Backend.claim returns 'in_progress'.
        matching_hash = request_fingerprint('POST', '/runs', b'one')
        backend.rows[('t1','u2','POST','/runs','busy')] = {'hash': matching_hash, 'owner': 'other-token'}
        with self.assertRaises(HTTPException) as busy:
            await service.execute(**{**args, 'principal_id':'u2', 'key':'busy'})
        self.assertEqual(busy.exception.detail['code'], 'IDEMPOTENCY_REQUEST_IN_PROGRESS')

if __name__ == '__main__': unittest.main()
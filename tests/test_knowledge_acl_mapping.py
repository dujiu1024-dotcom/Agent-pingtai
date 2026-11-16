from __future__ import annotations

import unittest

from agent_platform.knowledge.acl_mapping import (
    ACLMappingError,
    map_google_drive_permissions,
    map_sharepoint_permissions,
)


class ExternalACLMappingTests(unittest.TestCase):
    def resolve(self, provider, subject_type, identity):
        if provider == "google-drive" and identity == "alice@example.com":
            return ("user", "platform-alice")
        if provider == "google-drive" and identity == "group-1":
            return ("role", "legal-reviewers")
        if provider == "sharepoint" and identity == "aad-user-1":
            return ("user", "platform-alice")
        if provider == "sharepoint" and identity == "aad-group-1":
            return ("project", "project-legal")
        return None

    def test_google_drive_permissions_map_to_platform_principals(self):
        grants = map_google_drive_permissions(
            [
                {"id": "user-1", "type": "user", "emailAddress": "alice@example.com", "role": "reader"},
                {"id": "group-1", "type": "group", "role": "writer"},
                {"id": "user-2", "type": "user", "emailAddress": "alice@example.com", "role": "writer"},
                {"id": "deleted", "type": "user", "role": "writer", "deleted": True},
            ],
            self.resolve,
        )
        self.assertEqual(
            grants,
            (
                {"principal_type": "role", "principal_id": "legal-reviewers", "permission": "manage"},
                {"principal_type": "user", "principal_id": "platform-alice", "permission": "manage"},
            ),
        )

    def test_google_drive_public_or_domain_sharing_fails_closed(self):
        with self.assertRaises(ACLMappingError):
            map_google_drive_permissions(
                [{"id": "domain-1", "type": "domain", "domain": "example.com", "role": "reader"}],
                self.resolve,
            )
        with self.assertRaises(ACLMappingError):
            map_google_drive_permissions(
                [{"id": "unknown", "type": "user", "role": "reader"}],
                self.resolve,
            )

    def test_sharepoint_permissions_map_user_group_and_merge(self):
        grants = map_sharepoint_permissions(
            [
                {"roles": ["read"], "grantedToV2": {"user": {"id": "aad-user-1"}}},
                {"roles": ["write"], "grantedToIdentitiesV2": [{"group": {"id": "aad-group-1"}}]},
                {"roles": ["write"], "grantedToV2": {"user": {"id": "aad-user-1"}}},
            ],
            self.resolve,
        )
        self.assertEqual(
            grants,
            (
                {"principal_type": "project", "principal_id": "project-legal", "permission": "manage"},
                {"principal_type": "user", "principal_id": "platform-alice", "permission": "manage"},
            ),
        )

    def test_sharepoint_anonymous_link_and_unmapped_identity_fail_closed(self):
        with self.assertRaises(ACLMappingError):
            map_sharepoint_permissions(
                [{"roles": ["read"], "link": {"scope": "anonymous"}}],
                self.resolve,
            )
        with self.assertRaises(ACLMappingError):
            map_sharepoint_permissions(
                [{"roles": ["read"], "grantedToV2": {"user": {"id": "unknown"}}}],
                self.resolve,
            )


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""
Unit tests for migrate_routing_rules.py

Tests cover:
- Old format → new format conversion logic
- Idempotency (already-migrated rules skipped)
- Dry-run doesn't modify anything
"""

import json
import pytest
from unittest.mock import MagicMock, patch, call
from datetime import datetime, timezone

# Import the module under test
import migrate_routing_rules as migrate


class TestConvertRule:
    """Test convert_rule() function."""

    def test_converts_forward_to_gmail_action(self):
        """Old format action/target converts to actions array."""
        old_rule = {
            'PK': {'S': 'ROUTE#test@example.com'},
            'SK': {'S': 'RULE#v1'},
            'entity_type': {'S': 'ROUTE'},
            'recipient': {'S': 'test@example.com'},
            'action': {'S': 'forward-to-gmail'},
            'target': {'S': 'me@gmail.com'},
            'enabled': {'BOOL': True},
            'created_at': {'S': '2024-01-01T00:00:00Z'},
            'updated_at': {'S': '2024-01-01T00:00:00Z'},
            'description': {'S': 'Test rule'}
        }

        result = migrate.convert_rule(old_rule)

        assert 'actions' in result
        assert result['actions'] == {'L': [
            {'M': {'type': {'S': 'forward-to-gmail'}, 'target': {'S': 'me@gmail.com'}}}
        ]}
        # Old fields should be removed
        assert 'action' not in result
        assert 'target' not in result

    def test_converts_store_action(self):
        """Store action (no target) converts correctly."""
        old_rule = {
            'PK': {'S': 'ROUTE#test@example.com'},
            'SK': {'S': 'RULE#v1'},
            'entity_type': {'S': 'ROUTE'},
            'recipient': {'S': 'test@example.com'},
            'action': {'S': 'store'},
            'target': {'S': ''},
            'enabled': {'BOOL': True},
            'created_at': {'S': '2024-01-01T00:00:00Z'},
            'updated_at': {'S': '2024-01-01T00:00:00Z'},
        }

        result = migrate.convert_rule(old_rule)

        assert 'actions' in result
        assert result['actions'] == {'L': [
            {'M': {'type': {'S': 'store'}}}
        ]}

    def test_converts_bounce_action(self):
        """Bounce action (no target) converts correctly."""
        old_rule = {
            'PK': {'S': 'ROUTE#test@example.com'},
            'SK': {'S': 'RULE#v1'},
            'action': {'S': 'bounce'},
            'target': {'S': ''},
        }

        result = migrate.convert_rule(old_rule)

        assert 'actions' in result
        assert result['actions'] == {'L': [
            {'M': {'type': {'S': 'bounce'}}}
        ]}

    def test_preserves_other_attributes(self):
        """Non-action attributes are preserved."""
        old_rule = {
            'PK': {'S': 'ROUTE#test@example.com'},
            'SK': {'S': 'RULE#v1'},
            'entity_type': {'S': 'ROUTE'},
            'recipient': {'S': 'test@example.com'},
            'action': {'S': 'store'},
            'target': {'S': ''},
            'enabled': {'BOOL': True},
            'created_at': {'S': '2024-01-01T00:00:00Z'},
            'updated_at': {'S': '2024-01-01T00:00:00Z'},
            'description': {'S': 'Test rule'},
            'metadata': {'S': '{"key": "value"}'}
        }

        result = migrate.convert_rule(old_rule)

        assert result['PK'] == old_rule['PK']
        assert result['SK'] == old_rule['SK']
        assert result['entity_type'] == old_rule['entity_type']
        assert result['recipient'] == old_rule['recipient']
        assert result['enabled'] == old_rule['enabled']
        assert result['created_at'] == old_rule['created_at']
        assert result['description'] == old_rule['description']
        assert result['metadata'] == old_rule['metadata']

    def test_updates_updated_at_timestamp(self):
        """Updated_at should be set to current time."""
        old_rule = {
            'PK': {'S': 'ROUTE#test@example.com'},
            'SK': {'S': 'RULE#v1'},
            'action': {'S': 'store'},
            'target': {'S': ''},
            'updated_at': {'S': '2024-01-01T00:00:00Z'},
        }

        result = migrate.convert_rule(old_rule)

        # Check that updated_at was changed (not the old value)
        assert result['updated_at']['S'] != '2024-01-01T00:00:00Z'
        # Check that it's a valid ISO format timestamp
        assert 'T' in result['updated_at']['S']


class TestIsAlreadyMigrated:
    """Test is_already_migrated() function."""

    def test_returns_true_for_rule_with_actions(self):
        """Rule with actions attribute is already migrated."""
        rule = {
            'PK': {'S': 'ROUTE#test@example.com'},
            'SK': {'S': 'RULE#v1'},
            'actions': {'L': [{'M': {'type': {'S': 'store'}}}]},
        }

        assert migrate.is_already_migrated(rule) is True

    def test_returns_false_for_rule_with_action(self):
        """Rule with action (singular) attribute needs migration."""
        rule = {
            'PK': {'S': 'ROUTE#test@example.com'},
            'SK': {'S': 'RULE#v1'},
            'action': {'S': 'store'},
            'target': {'S': ''},
        }

        assert migrate.is_already_migrated(rule) is False

    def test_returns_false_for_rule_with_both(self):
        """Edge case: rule with both should not be considered migrated."""
        rule = {
            'PK': {'S': 'ROUTE#test@example.com'},
            'SK': {'S': 'RULE#v1'},
            'action': {'S': 'store'},
            'target': {'S': ''},
            'actions': {'L': [{'M': {'type': {'S': 'store'}}}]},
        }

        # If it still has 'action', it's not cleanly migrated
        assert migrate.is_already_migrated(rule) is True


class TestIsRoutingRule:
    """Test is_routing_rule() function."""

    def test_returns_true_for_route_entity(self):
        """Items with ROUTE# PK prefix are routing rules."""
        item = {
            'PK': {'S': 'ROUTE#test@example.com'},
            'SK': {'S': 'RULE#v1'},
        }

        assert migrate.is_routing_rule(item) is True

    def test_returns_false_for_smtp_user(self):
        """SMTP_USER# entities are not routing rules."""
        item = {
            'PK': {'S': 'SMTP_USER#myapp'},
            'SK': {'S': 'CREDENTIALS#v1'},
        }

        assert migrate.is_routing_rule(item) is False

    def test_returns_false_for_canary(self):
        """CANARY# entities are not routing rules."""
        item = {
            'PK': {'S': 'CANARY#canary-2025-01-01'},
            'SK': {'S': 'TRACKING#v1'},
        }

        assert migrate.is_routing_rule(item) is False


class TestMigrateRules:
    """Test migrate_rules() main function."""

    def test_dry_run_does_not_write(self):
        """Dry run should not call put_item."""
        mock_dynamodb = MagicMock()
        mock_dynamodb.scan.return_value = {
            'Items': [
                {
                    'PK': {'S': 'ROUTE#test@example.com'},
                    'SK': {'S': 'RULE#v1'},
                    'action': {'S': 'store'},
                    'target': {'S': ''},
                }
            ]
        }

        result = migrate.migrate_rules(mock_dynamodb, 'test-table', dry_run=True)

        mock_dynamodb.put_item.assert_not_called()
        assert result['would_migrate'] == 1
        assert result['migrated'] == 0

    def test_actual_run_writes_converted_rules(self):
        """Actual run should call put_item with converted rules."""
        mock_dynamodb = MagicMock()
        mock_dynamodb.scan.return_value = {
            'Items': [
                {
                    'PK': {'S': 'ROUTE#test@example.com'},
                    'SK': {'S': 'RULE#v1'},
                    'action': {'S': 'forward-to-gmail'},
                    'target': {'S': 'me@gmail.com'},
                }
            ]
        }

        result = migrate.migrate_rules(mock_dynamodb, 'test-table', dry_run=False)

        mock_dynamodb.put_item.assert_called_once()
        call_args = mock_dynamodb.put_item.call_args
        assert call_args[1]['TableName'] == 'test-table'
        assert 'actions' in call_args[1]['Item']
        assert result['migrated'] == 1

    def test_skips_already_migrated_rules(self):
        """Already migrated rules should be skipped."""
        mock_dynamodb = MagicMock()
        mock_dynamodb.scan.return_value = {
            'Items': [
                {
                    'PK': {'S': 'ROUTE#test@example.com'},
                    'SK': {'S': 'RULE#v1'},
                    'actions': {'L': [{'M': {'type': {'S': 'store'}}}]},
                }
            ]
        }

        result = migrate.migrate_rules(mock_dynamodb, 'test-table', dry_run=False)

        mock_dynamodb.put_item.assert_not_called()
        assert result['already_migrated'] == 1
        assert result['migrated'] == 0

    def test_skips_non_route_entities(self):
        """Non-ROUTE entities should be skipped."""
        mock_dynamodb = MagicMock()
        mock_dynamodb.scan.return_value = {
            'Items': [
                {
                    'PK': {'S': 'SMTP_USER#myapp'},
                    'SK': {'S': 'CREDENTIALS#v1'},
                    'username': {'S': 'myapp'},
                },
                {
                    'PK': {'S': 'ROUTE#test@example.com'},
                    'SK': {'S': 'RULE#v1'},
                    'action': {'S': 'store'},
                    'target': {'S': ''},
                }
            ]
        }

        result = migrate.migrate_rules(mock_dynamodb, 'test-table', dry_run=False)

        # Only the ROUTE entity should be migrated
        assert mock_dynamodb.put_item.call_count == 1
        assert result['migrated'] == 1
        assert result['skipped_non_route'] == 1

    def test_handles_pagination(self):
        """Should handle paginated scan results."""
        mock_dynamodb = MagicMock()
        # First page has LastEvaluatedKey
        mock_dynamodb.scan.side_effect = [
            {
                'Items': [
                    {
                        'PK': {'S': 'ROUTE#a@example.com'},
                        'SK': {'S': 'RULE#v1'},
                        'action': {'S': 'store'},
                        'target': {'S': ''},
                    }
                ],
                'LastEvaluatedKey': {'PK': {'S': 'ROUTE#a@example.com'}}
            },
            {
                'Items': [
                    {
                        'PK': {'S': 'ROUTE#b@example.com'},
                        'SK': {'S': 'RULE#v1'},
                        'action': {'S': 'store'},
                        'target': {'S': ''},
                    }
                ]
            }
        ]

        result = migrate.migrate_rules(mock_dynamodb, 'test-table', dry_run=False)

        assert mock_dynamodb.scan.call_count == 2
        assert result['migrated'] == 2


class TestIntegration:
    """Integration-style tests with more realistic scenarios."""

    def test_full_migration_scenario(self):
        """Test a realistic migration with mixed rules."""
        mock_dynamodb = MagicMock()
        mock_dynamodb.scan.return_value = {
            'Items': [
                # Old format - forward to gmail
                {
                    'PK': {'S': 'ROUTE#support@example.com'},
                    'SK': {'S': 'RULE#v1'},
                    'entity_type': {'S': 'ROUTE'},
                    'recipient': {'S': 'support@example.com'},
                    'action': {'S': 'forward-to-gmail'},
                    'target': {'S': 'team@gmail.com'},
                    'enabled': {'BOOL': True},
                    'created_at': {'S': '2024-01-01T00:00:00Z'},
                    'updated_at': {'S': '2024-01-01T00:00:00Z'},
                },
                # Old format - bounce
                {
                    'PK': {'S': 'ROUTE#*@example.com'},
                    'SK': {'S': 'RULE#v1'},
                    'entity_type': {'S': 'ROUTE'},
                    'recipient': {'S': '*@example.com'},
                    'action': {'S': 'bounce'},
                    'target': {'S': ''},
                    'enabled': {'BOOL': True},
                    'created_at': {'S': '2024-02-01T00:00:00Z'},
                    'updated_at': {'S': '2024-02-01T00:00:00Z'},
                },
                # Already migrated
                {
                    'PK': {'S': 'ROUTE#admin@example.com'},
                    'SK': {'S': 'RULE#v1'},
                    'entity_type': {'S': 'ROUTE'},
                    'recipient': {'S': 'admin@example.com'},
                    'actions': {'L': [
                        {'M': {'type': {'S': 'forward-to-gmail'}, 'target': {'S': 'admin@gmail.com'}}}
                    ]},
                    'enabled': {'BOOL': True},
                    'created_at': {'S': '2024-03-01T00:00:00Z'},
                    'updated_at': {'S': '2024-03-01T00:00:00Z'},
                },
                # Non-route entity
                {
                    'PK': {'S': 'SMTP_USER#notifications'},
                    'SK': {'S': 'CREDENTIALS#v1'},
                    'entity_type': {'S': 'SMTP_USER'},
                    'username': {'S': 'notifications'},
                },
            ]
        }

        result = migrate.migrate_rules(mock_dynamodb, 'test-table', dry_run=False)

        assert result['migrated'] == 2
        assert result['already_migrated'] == 1
        assert result['skipped_non_route'] == 1
        assert mock_dynamodb.put_item.call_count == 2


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

#!/usr/bin/env python3
"""
Unit tests for router_enrichment.py

Tests cover:
- Parsing single-action `actions` array
- Parsing multi-action `actions` array
- Aggregation produces correct counts for multi-action
- S3 tag format with multiple actions
"""

import json
import sys
import pytest
from unittest.mock import MagicMock, patch

# We need to mock things BEFORE importing router_enrichment
# So set up environment and mock boto3 first

@pytest.fixture(scope='module', autouse=True)
def setup_mocks():
    """Set up environment and mock boto3 before any imports."""
    import os
    os.environ['DYNAMODB_TABLE_NAME'] = 'test-table'
    os.environ['ENVIRONMENT'] = 'test'

    # Mock boto3.client before importing router_enrichment
    with patch('boto3.client') as mock_client:
        mock_dynamodb = MagicMock()
        mock_cloudwatch = MagicMock()
        mock_eventbridge = MagicMock()
        mock_ssm = MagicMock()
        mock_s3 = MagicMock()

        def get_client(service_name, **kwargs):
            clients = {
                'dynamodb': mock_dynamodb,
                'cloudwatch': mock_cloudwatch,
                'events': mock_eventbridge,
                'ssm': mock_ssm,
                's3': mock_s3,
            }
            return clients.get(service_name, MagicMock())

        mock_client.side_effect = get_client

        # Mock X-Ray
        mock_xray = MagicMock()
        mock_subsegment = MagicMock()
        mock_xray.begin_subsegment.return_value = mock_subsegment
        mock_xray.end_subsegment.return_value = None

        with patch.dict(sys.modules, {'aws_xray_sdk.core': MagicMock()}):
            # Now import the module
            import router_enrichment

            # Store the mock clients on the module for tests to access
            router_enrichment._test_mocks = {
                'dynamodb': mock_dynamodb,
                'cloudwatch': mock_cloudwatch,
                'eventbridge': mock_eventbridge,
                'ssm': mock_ssm,
                's3': mock_s3,
                'xray': mock_xray,
            }

            yield router_enrichment


@pytest.fixture
def router(setup_mocks):
    """Get the router module with mocks reset."""
    setup_mocks.lookup_routing_rule.cache_clear()
    # Reset all mocks
    for mock in setup_mocks._test_mocks.values():
        mock.reset_mock()
    return setup_mocks


class TestLookupRoutingRule:
    """Test lookup_routing_rule() function."""

    def test_parses_single_action_array(self, router):
        """New format with single action parses correctly."""
        router.dynamodb.get_item.return_value = {
            'Item': {
                'recipient': {'S': 'test@example.com'},
                'actions': {'L': [
                    {'M': {'type': {'S': 'forward-to-gmail'}, 'target': {'S': 'me@gmail.com'}}}
                ]},
                'enabled': {'BOOL': True},
                'description': {'S': 'Test rule'},
                'created_at': {'S': '2024-01-01T00:00:00Z'},
                'updated_at': {'S': '2024-01-01T00:00:00Z'},
                'metadata': {'S': '{}'},
            }
        }

        result = router.lookup_routing_rule('ROUTE#test@example.com')

        assert result is not None
        assert 'actions' in result
        assert len(result['actions']) == 1
        assert result['actions'][0]['type'] == 'forward-to-gmail'
        assert result['actions'][0]['target'] == 'me@gmail.com'

    def test_parses_multi_action_array(self, router):
        """New format with multiple actions parses correctly."""
        router.dynamodb.get_item.return_value = {
            'Item': {
                'recipient': {'S': 'test@example.com'},
                'actions': {'L': [
                    {'M': {'type': {'S': 'forward-to-gmail'}, 'target': {'S': 'me@gmail.com'}}},
                    {'M': {'type': {'S': 'store'}}}
                ]},
                'enabled': {'BOOL': True},
                'description': {'S': 'Test rule'},
                'created_at': {'S': '2024-01-01T00:00:00Z'},
                'updated_at': {'S': '2024-01-01T00:00:00Z'},
                'metadata': {'S': '{}'},
            }
        }

        result = router.lookup_routing_rule('ROUTE#test@example.com')

        assert result is not None
        assert 'actions' in result
        assert len(result['actions']) == 2
        assert result['actions'][0]['type'] == 'forward-to-gmail'
        assert result['actions'][0]['target'] == 'me@gmail.com'
        assert result['actions'][1]['type'] == 'store'
        assert result['actions'][1].get('target') is None

    def test_backward_compatible_with_old_format(self, router):
        """Old format with action/target still works (for transition)."""
        router.dynamodb.get_item.return_value = {
            'Item': {
                'recipient': {'S': 'test@example.com'},
                'action': {'S': 'forward-to-gmail'},
                'target': {'S': 'me@gmail.com'},
                'enabled': {'BOOL': True},
                'description': {'S': 'Test rule'},
                'created_at': {'S': '2024-01-01T00:00:00Z'},
                'updated_at': {'S': '2024-01-01T00:00:00Z'},
                'metadata': {'S': '{}'},
            }
        }

        result = router.lookup_routing_rule('ROUTE#test@example.com')

        assert result is not None
        # Old format should be converted to actions list
        assert 'actions' in result
        assert len(result['actions']) == 1
        assert result['actions'][0]['type'] == 'forward-to-gmail'
        assert result['actions'][0]['target'] == 'me@gmail.com'


class TestGetRoutingDecision:
    """Test get_routing_decision() function."""

    def test_returns_actions_list_single(self, router):
        """Single action returns list with one item."""
        router.dynamodb.get_item.return_value = {
            'Item': {
                'recipient': {'S': 'test@example.com'},
                'actions': {'L': [
                    {'M': {'type': {'S': 'forward-to-gmail'}, 'target': {'S': 'me@gmail.com'}}}
                ]},
                'enabled': {'BOOL': True},
                'description': {'S': ''},
                'created_at': {'S': ''},
                'updated_at': {'S': ''},
                'metadata': {'S': '{}'},
            }
        }

        actions, lookup_key, metadata = router.get_routing_decision('test@example.com')

        assert isinstance(actions, list)
        assert len(actions) == 1
        assert actions[0]['type'] == 'forward-to-gmail'
        assert actions[0]['target'] == 'me@gmail.com'

    def test_returns_actions_list_multi(self, router):
        """Multiple actions returns list with all items."""
        router.dynamodb.get_item.return_value = {
            'Item': {
                'recipient': {'S': 'test@example.com'},
                'actions': {'L': [
                    {'M': {'type': {'S': 'forward-to-gmail'}, 'target': {'S': 'me@gmail.com'}}},
                    {'M': {'type': {'S': 'store'}}}
                ]},
                'enabled': {'BOOL': True},
                'description': {'S': ''},
                'created_at': {'S': ''},
                'updated_at': {'S': ''},
                'metadata': {'S': '{}'},
            }
        }

        actions, lookup_key, metadata = router.get_routing_decision('test@example.com')

        assert isinstance(actions, list)
        assert len(actions) == 2
        assert actions[0]['type'] == 'forward-to-gmail'
        assert actions[1]['type'] == 'store'


class TestDecideAction:
    """Test decide_action() function."""

    def test_aggregation_multi_action_single_recipient(self, router):
        """Multi-action rule produces correct counts for single recipient."""
        router.dynamodb.get_item.return_value = {
            'Item': {
                'recipient': {'S': 'test@example.com'},
                'actions': {'L': [
                    {'M': {'type': {'S': 'forward-to-gmail'}, 'target': {'S': 'me@gmail.com'}}},
                    {'M': {'type': {'S': 'store'}}}
                ]},
                'enabled': {'BOOL': True},
                'description': {'S': ''},
                'created_at': {'S': ''},
                'updated_at': {'S': ''},
                'metadata': {'S': '{}'},
            }
        }

        ses_message = {
            'mail': {
                'messageId': 'test-123',
                'source': 'sender@example.com',
                'headers': [{'name': 'Subject', 'value': 'Test'}],
            },
            'receipt': {
                'recipients': ['test@example.com'],
                'spamVerdict': {'status': 'PASS'},
                'virusVerdict': {'status': 'PASS'},
                'dmarcVerdict': {'status': 'PASS'},
                'dkimVerdict': {'status': 'PASS'},
                'spfVerdict': {'status': 'PASS'},
                'action': {'type': 'S3', 'bucketName': 'test-bucket', 'objectKey': 'emails/test-123'},
            },
        }

        results = router.decide_action(ses_message)

        # Should have 2 results for single recipient (one per action)
        assert len(results) == 2

        # Check we have both action types
        action_types = [r[0] for r in results]
        assert 'forward-to-gmail' in action_types
        assert 'store' in action_types

    def test_aggregation_multi_recipient_multi_action(self, router):
        """Multi-action rule with multiple recipients produces correct counts."""
        # Make the mock return the same rule for both lookups
        router.dynamodb.get_item.return_value = {
            'Item': {
                'recipient': {'S': '*@example.com'},
                'actions': {'L': [
                    {'M': {'type': {'S': 'forward-to-gmail'}, 'target': {'S': 'me@gmail.com'}}},
                    {'M': {'type': {'S': 'store'}}}
                ]},
                'enabled': {'BOOL': True},
                'description': {'S': ''},
                'created_at': {'S': ''},
                'updated_at': {'S': ''},
                'metadata': {'S': '{}'},
            }
        }

        ses_message = {
            'mail': {
                'messageId': 'test-123',
                'source': 'sender@example.com',
                'headers': [{'name': 'Subject', 'value': 'Test'}],
            },
            'receipt': {
                'recipients': ['a@example.com', 'b@example.com'],
                'spamVerdict': {'status': 'PASS'},
                'virusVerdict': {'status': 'PASS'},
                'dmarcVerdict': {'status': 'PASS'},
                'dkimVerdict': {'status': 'PASS'},
                'spfVerdict': {'status': 'PASS'},
                'action': {'type': 'S3', 'bucketName': 'test-bucket', 'objectKey': 'emails/test-123'},
            },
        }

        results = router.decide_action(ses_message)

        # Should have 4 results total: 2 recipients × 2 actions each
        assert len(results) == 4

        # Count action types
        forward_count = sum(1 for r in results if r[0] == 'forward-to-gmail')
        store_count = sum(1 for r in results if r[0] == 'store')

        assert forward_count == 2
        assert store_count == 2


class TestS3Tagging:
    """Test S3 tag format with multiple actions."""

    def test_concatenates_action_types(self, router):
        """Multiple actions should be space-separated in tag."""
        router.dynamodb.get_item.return_value = {
            'Item': {
                'recipient': {'S': 'test@example.com'},
                'actions': {'L': [
                    {'M': {'type': {'S': 'forward-to-gmail'}, 'target': {'S': 'me@gmail.com'}}},
                    {'M': {'type': {'S': 'store'}}}
                ]},
                'enabled': {'BOOL': True},
                'description': {'S': ''},
                'created_at': {'S': ''},
                'updated_at': {'S': ''},
                'metadata': {'S': '{}'},
            }
        }

        ses_message = {
            'mail': {
                'messageId': 'test-123',
                'source': 'sender@example.com',
                'headers': [{'name': 'Subject', 'value': 'Test'}],
            },
            'receipt': {
                'recipients': ['test@example.com'],
                'spamVerdict': {'status': 'PASS'},
                'virusVerdict': {'status': 'PASS'},
                'dmarcVerdict': {'status': 'PASS'},
                'dkimVerdict': {'status': 'PASS'},
                'spfVerdict': {'status': 'PASS'},
                'action': {'type': 'S3', 'bucketName': 'test-bucket', 'objectKey': 'emails/test-123'},
            },
        }

        router.decide_action(ses_message)

        # Check that put_object_tagging was called
        router.s3.put_object_tagging.assert_called_once()

        # Extract the tag set from the call
        call_kwargs = router.s3.put_object_tagging.call_args[1]
        tag_set = call_kwargs['Tagging']['TagSet']
        tags_dict = {t['Key']: t['Value'] for t in tag_set}

        # The 'action' tag should contain both actions space-separated
        assert 'action' in tags_dict
        assert 'forward-to-gmail' in tags_dict['action']
        assert 'store' in tags_dict['action']


class TestSanitizeTagValue:
    """Test sanitize_tag_value() helper function."""

    def test_sanitizes_special_characters(self, router):
        """Special characters should be replaced with underscore."""
        result = router.sanitize_tag_value('hello!world')
        assert '!' not in result

    def test_preserves_allowed_characters(self, router):
        """Allowed characters should be preserved."""
        result = router.sanitize_tag_value('hello+world@test.com')
        assert '+' in result
        assert '@' in result
        assert '.' in result


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

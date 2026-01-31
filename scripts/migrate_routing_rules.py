#!/usr/bin/env python3
"""
Migration script: Convert routing rules from old to new format.

Old format:
    {
        "PK": "ROUTE#support@example.com",
        "SK": "RULE#v1",
        "action": "forward-to-gmail",
        "target": "me@gmail.com",
        ...
    }

New format:
    {
        "PK": "ROUTE#support@example.com",
        "SK": "RULE#v1",
        "actions": [
            {"type": "forward-to-gmail", "target": "me@gmail.com"}
        ],
        ...
    }

Usage:
    # Dry run (preview changes)
    python3 scripts/migrate_routing_rules.py --env test --dry-run

    # Actual migration
    python3 scripts/migrate_routing_rules.py --env test
"""

import argparse
import sys
from datetime import datetime, timezone
from typing import Any, Dict

try:
    import boto3
    from botocore.exceptions import ClientError
except ImportError as e:
    print("=" * 80, file=sys.stderr)
    print("MIGRATION SCRIPT DID NOT RUN - Missing Python dependencies", file=sys.stderr)
    print("=" * 80, file=sys.stderr)
    print("", file=sys.stderr)
    print(f"Missing package: {e.name}", file=sys.stderr)
    print("", file=sys.stderr)
    print("You MUST activate the virtual environment before running:", file=sys.stderr)
    print("", file=sys.stderr)
    print("  source .venv/bin/activate", file=sys.stderr)
    print("  python3 scripts/migrate_routing_rules.py --env test --dry-run", file=sys.stderr)
    print("", file=sys.stderr)
    print("=" * 80, file=sys.stderr)
    sys.exit(1)


def is_routing_rule(item: Dict[str, Any]) -> bool:
    """
    Check if a DynamoDB item is a routing rule.

    Args:
        item: DynamoDB item (with type descriptors)

    Returns:
        bool: True if this is a ROUTE entity
    """
    pk = item.get('PK', {}).get('S', '')
    return pk.startswith('ROUTE#')


def is_already_migrated(item: Dict[str, Any]) -> bool:
    """
    Check if a routing rule has already been migrated.

    A rule is considered migrated if it has an 'actions' (plural) attribute.

    Args:
        item: DynamoDB item (with type descriptors)

    Returns:
        bool: True if already migrated
    """
    return 'actions' in item


def convert_rule(old_item: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert a routing rule from old format to new format.

    Args:
        old_item: DynamoDB item in old format (with type descriptors)

    Returns:
        dict: DynamoDB item in new format (with type descriptors)
    """
    # Start with a copy
    new_item = {}

    # Copy all attributes except action/target
    for key, value in old_item.items():
        if key not in ('action', 'target'):
            new_item[key] = value

    # Convert action/target to actions array
    old_action = old_item.get('action', {}).get('S', 'store')
    old_target = old_item.get('target', {}).get('S', '')

    # Build the action object
    action_obj = {'type': {'S': old_action}}
    if old_target:
        action_obj['target'] = {'S': old_target}

    # Create actions array
    new_item['actions'] = {'L': [{'M': action_obj}]}

    # Update the updated_at timestamp
    new_item['updated_at'] = {'S': datetime.now(timezone.utc).isoformat()}

    return new_item


def migrate_rules(
    dynamodb_client,
    table_name: str,
    dry_run: bool = True
) -> Dict[str, int]:
    """
    Migrate all routing rules from old to new format.

    Args:
        dynamodb_client: boto3 DynamoDB client
        table_name: Name of the DynamoDB table
        dry_run: If True, don't actually write changes

    Returns:
        dict: Statistics about the migration
    """
    stats = {
        'scanned': 0,
        'migrated': 0,
        'would_migrate': 0,
        'already_migrated': 0,
        'skipped_non_route': 0,
        'errors': 0,
    }

    # Scan the entire table
    scan_kwargs = {'TableName': table_name}
    last_evaluated_key = None

    while True:
        if last_evaluated_key:
            scan_kwargs['ExclusiveStartKey'] = last_evaluated_key

        response = dynamodb_client.scan(**scan_kwargs)
        items = response.get('Items', [])

        for item in items:
            stats['scanned'] += 1
            pk = item.get('PK', {}).get('S', 'unknown')

            # Skip non-route entities
            if not is_routing_rule(item):
                stats['skipped_non_route'] += 1
                if not dry_run:
                    print(f"  SKIP (non-route): {pk}")
                continue

            # Skip already migrated
            if is_already_migrated(item):
                stats['already_migrated'] += 1
                if not dry_run:
                    print(f"  SKIP (already migrated): {pk}")
                continue

            # Convert the rule
            new_item = convert_rule(item)

            if dry_run:
                stats['would_migrate'] += 1
                print(f"  WOULD MIGRATE: {pk}")
                old_action = item.get('action', {}).get('S', 'unknown')
                old_target = item.get('target', {}).get('S', '')
                print(f"    Old: action={old_action}, target={old_target}")
                actions = new_item.get('actions', {}).get('L', [])
                if actions:
                    new_action = actions[0].get('M', {})
                    new_type = new_action.get('type', {}).get('S', 'unknown')
                    new_target = new_action.get('target', {}).get('S', '')
                    print(f"    New: actions=[{{type={new_type}, target={new_target}}}]")
            else:
                try:
                    dynamodb_client.put_item(
                        TableName=table_name,
                        Item=new_item
                    )
                    stats['migrated'] += 1
                    print(f"  MIGRATED: {pk}")
                except ClientError as e:
                    stats['errors'] += 1
                    print(f"  ERROR: {pk} - {e}")

        # Check for pagination
        last_evaluated_key = response.get('LastEvaluatedKey')
        if not last_evaluated_key:
            break

    return stats


def main():
    parser = argparse.ArgumentParser(
        description='Migrate routing rules from old to new format'
    )
    parser.add_argument(
        '--env',
        required=True,
        choices=['test', 'prod'],
        help='Environment (test or prod)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Preview changes without writing'
    )
    parser.add_argument(
        '--region',
        default='ap-southeast-2',
        help='AWS region (default: ap-southeast-2)'
    )

    args = parser.parse_args()

    table_name = f'ses-mail-email-routing-{args.env}'

    print("=" * 60)
    print("Routing Rules Migration")
    print("=" * 60)
    print(f"Environment: {args.env}")
    print(f"Table: {table_name}")
    print(f"Region: {args.region}")
    print(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE'}")
    print("=" * 60)

    if not args.dry_run:
        response = input("\nThis will modify the database. Continue? [y/N]: ")
        if response.lower() != 'y':
            print("Aborted.")
            sys.exit(0)

    print("\nScanning table...")
    dynamodb = boto3.client('dynamodb', region_name=args.region)

    stats = migrate_rules(dynamodb, table_name, dry_run=args.dry_run)

    print("\n" + "=" * 60)
    print("Migration Summary")
    print("=" * 60)
    print(f"Items scanned:       {stats['scanned']}")
    print(f"Non-route skipped:   {stats['skipped_non_route']}")
    print(f"Already migrated:    {stats['already_migrated']}")
    if args.dry_run:
        print(f"Would migrate:       {stats['would_migrate']}")
    else:
        print(f"Migrated:            {stats['migrated']}")
        print(f"Errors:              {stats['errors']}")
    print("=" * 60)

    if args.dry_run and stats['would_migrate'] > 0:
        print("\nTo apply these changes, run without --dry-run")

    if stats['errors'] > 0:
        sys.exit(1)


if __name__ == '__main__':
    main()

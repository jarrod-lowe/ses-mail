# Step Functions Testing with Python DSL

## Overview

Implement a Python DSL as the single source of truth for AWS Step Functions that generates both:

1. YAML files for Terraform deployment
2. Test contracts for pre-deployment validation using AWS TestState API

This eliminates drift between state machine definitions and test contracts.

## Architecture

```plain
┌─────────────────────────────────────┐
│ Python DSL Definitions              │
│ stepfunctions/token_monitor.py      │
│ stepfunctions/retry_processor.py    │
└──────────────┬──────────────────────┘
               │ (single source of truth)
               │
    ┌──────────┴─────────┐
    ↓                    ↓
┌─────────────┐    ┌──────────────────┐
│ YAML Files  │    │ Test Contracts   │
│ (generated) │    │ (auto-derived)   │
└─────────────┘    └──────────────────┘
    │                    │
    ↓                    ↓
Terraform           TestState API
Deployment          Pre-deploy Tests
```

## File Structure

```plain
ses-mail/
├── stepfunctions/                    # NEW: DSL definitions (source of truth)
│   ├── __init__.py
│   ├── token_monitor.py             # Token monitor state machine
│   └── retry_processor.py           # Retry processor state machine (later)
│
├── stepfunctions_dsl/               # NEW: DSL library
│   ├── __init__.py
│   ├── core.py                      # StateMachine, State base classes
│   ├── states.py                    # TaskState, PassState, ChoiceState, etc.
│   ├── resources.py                 # AWS service integrations (SQS, Lambda, SSM)
│   ├── error_handling.py            # Retry, Catch definitions
│   └── contracts.py                 # Contract generation for tests
│
├── scripts/
│   ├── generate_stepfunctions.py    # NEW: Generate YAML from DSL
│   └── integration_test.py          # Existing integration tests
│
├── tests/stepfunctions/             # NEW: Unit tests
│   ├── __init__.py
│   ├── test_token_monitor.py        # Tests for token monitor
│   ├── test_retry_processor.py      # Tests for retry processor (later)
│   └── helpers.py                   # TestState CLI wrapper utilities
│
├── terraform/modules/ses-mail/stepfunctions/
│   ├── retry-processor.yaml         # GENERATED (keep in git for now)
│   └── token-monitor.yaml           # GENERATED (keep in git for now)
│
├── Makefile                         # MODIFIED: Add generation + test targets
└── requirements.txt                 # MODIFIED: Add pytest
```

## Phase 1: Build Minimal DSL Library

### File: `stepfunctions_dsl/core.py`

Core classes for state machine structure:

```python
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
import yaml

@dataclass
class State:
    """Base class for all state types"""
    name: str

    def to_asl(self) -> Dict[str, Any]:
        """Convert to Amazon States Language dict"""
        raise NotImplementedError

    def next_state(self) -> Optional[str]:
        """Return the next state name, if any"""
        return None

@dataclass
class StateMachine:
    """Top-level state machine definition"""
    comment: str
    start_at: str
    states: List[State]
    query_language: Optional[str] = None  # For JSONata support

    def to_asl(self) -> Dict[str, Any]:
        """Convert to Amazon States Language"""
        asl = {
            "Comment": self.comment,
            "StartAt": self.start_at,
            "States": {}
        }

        if self.query_language:
            asl["QueryLanguage"] = self.query_language

        # Build states dict
        for state in self.states:
            asl["States"][state.name] = state.to_asl()

        return asl

    def to_yaml(self, path: str) -> None:
        """Generate YAML file for Terraform"""
        asl = self.to_asl()
        with open(path, 'w') as f:
            yaml.dump(asl, f, sort_keys=False, default_flow_style=False)

    def get_state(self, name: str) -> State:
        """Get state by name (for testing)"""
        for state in self.states:
            if state.name == name:
                return state
        raise ValueError(f"State '{name}' not found")
```

### File: `stepfunctions_dsl/states.py`

State type implementations:

```python
from typing import Any, Dict, List, Optional, Union
from dataclasses import dataclass, field
from .core import State
from .error_handling import Catch, Retry

@dataclass
class TaskState(State):
    """Task state - executes work via AWS service integration"""
    resource: str
    arguments: Optional[Dict[str, Any]] = None  # For JSONata Arguments
    parameters: Optional[Dict[str, Any]] = None  # For standard Parameters
    result_path: Optional[str] = None
    next: Optional[str] = None
    end: bool = False
    catch: List[Catch] = field(default_factory=list)
    retry: List[Retry] = field(default_factory=list)
    timeout_seconds: Optional[int] = None

    def to_asl(self) -> Dict[str, Any]:
        asl = {"Type": "Task", "Resource": self.resource}

        if self.arguments:
            asl["Arguments"] = self.arguments
        if self.parameters:
            asl["Parameters"] = self.parameters
        if self.result_path:
            asl["ResultPath"] = self.result_path
        if self.next:
            asl["Next"] = self.next
        if self.end:
            asl["End"] = True
        if self.timeout_seconds:
            asl["TimeoutSeconds"] = self.timeout_seconds
        if self.catch:
            asl["Catch"] = [c.to_asl() for c in self.catch]
        if self.retry:
            asl["Retry"] = [r.to_asl() for r in self.retry]

        return asl

    def next_state(self) -> Optional[str]:
        return self.next

@dataclass
class PassState(State):
    """Pass state - transforms data or injects values"""
    output: Optional[Dict[str, Any]] = None  # For JSONata Output
    parameters: Optional[Dict[str, Any]] = None
    result: Optional[Any] = None
    result_path: Optional[str] = None
    next: Optional[str] = None
    end: bool = False

    def to_asl(self) -> Dict[str, Any]:
        asl = {"Type": "Pass"}

        if self.output:
            asl["Output"] = self.output
        if self.parameters:
            asl["Parameters"] = self.parameters
        if self.result is not None:
            asl["Result"] = self.result
        if self.result_path:
            asl["ResultPath"] = self.result_path
        if self.next:
            asl["Next"] = self.next
        if self.end:
            asl["End"] = True

        return asl

    def next_state(self) -> Optional[str]:
        return self.next

@dataclass
class SucceedState(State):
    """Terminal success state"""

    def to_asl(self) -> Dict[str, Any]:
        return {"Type": "Succeed"}

@dataclass
class FailState(State):
    """Terminal failure state"""
    error: str
    cause: str

    def to_asl(self) -> Dict[str, Any]:
        return {
            "Type": "Fail",
            "Error": self.error,
            "Cause": self.cause
        }

@dataclass
class ChoiceRule:
    """Single choice rule"""
    variable: Optional[str] = None
    is_present: Optional[bool] = None
    next: Optional[str] = None

    def to_asl(self) -> Dict[str, Any]:
        asl = {}
        if self.variable:
            asl["Variable"] = self.variable
        if self.is_present is not None:
            asl["IsPresent"] = self.is_present
        if self.next:
            asl["Next"] = self.next
        return asl

@dataclass
class ChoiceState(State):
    """Choice state - conditional branching"""
    choices: List[ChoiceRule]
    default: Optional[str] = None

    def to_asl(self) -> Dict[str, Any]:
        asl = {
            "Type": "Choice",
            "Choices": [c.to_asl() for c in self.choices]
        }
        if self.default:
            asl["Default"] = self.default
        return asl

@dataclass
class MapState(State):
    """Map state - parallel iteration"""
    items_path: str
    max_concurrency: int
    result_path: Optional[str] = None
    iterator: 'StateMachine' = None  # Forward reference
    next: Optional[str] = None

    def to_asl(self) -> Dict[str, Any]:
        from .core import StateMachine

        asl = {
            "Type": "Map",
            "ItemsPath": self.items_path,
            "MaxConcurrency": self.max_concurrency,
            "Iterator": self.iterator.to_asl()
        }

        if self.result_path:
            asl["ResultPath"] = self.result_path
        if self.next:
            asl["Next"] = self.next

        return asl

    def next_state(self) -> Optional[str]:
        return self.next
```

### File: `stepfunctions_dsl/error_handling.py`

```python
from typing import Any, Dict, List, Optional
from dataclasses import dataclass

@dataclass
class Retry:
    """Retry configuration"""
    error_equals: List[str]
    interval_seconds: int
    max_attempts: int
    backoff_rate: float

    def to_asl(self) -> Dict[str, Any]:
        return {
            "ErrorEquals": self.error_equals,
            "IntervalSeconds": self.interval_seconds,
            "MaxAttempts": self.max_attempts,
            "BackoffRate": self.backoff_rate
        }

@dataclass
class Catch:
    """Catch error handler"""
    error_equals: List[str]
    next: str
    result_path: Optional[str] = None

    def to_asl(self) -> Dict[str, Any]:
        asl = {
            "ErrorEquals": self.error_equals,
            "Next": self.next
        }
        if self.result_path:
            asl["ResultPath"] = self.result_path
        return asl
```

## Phase 2: Define Token Monitor in DSL

### File: `stepfunctions/token_monitor.py`

```python
from stepfunctions_dsl.core import StateMachine
from stepfunctions_dsl.states import TaskState, PassState, SucceedState, FailState
from stepfunctions_dsl.error_handling import Catch

def create_token_monitor(parameter_name: str, environment: str) -> StateMachine:
    """
    Create token monitor state machine.

    Args:
        parameter_name: SSM parameter name (e.g., "/ses-mail/test/gmail-token")
        environment: Environment name (e.g., "test")

    Returns:
        StateMachine ready to generate YAML
    """
    return StateMachine(
        comment="Monitor Gmail OAuth token expiration every 5 minutes",
        query_language="JSONata",
        start_at="GetTokenParameter",
        states=[
            # Get SSM parameter containing token metadata
            TaskState(
                name="GetTokenParameter",
                resource="arn:aws:states:::aws-sdk:ssm:getParameter",
                arguments={
                    "Name": parameter_name,
                    "WithDecryption": True
                },
                next="CalculateExpiration",
                catch=[
                    Catch(
                        error_equals=["Ssm.ParameterNotFound"],
                        next="HandleMissingParameter"
                    ),
                    Catch(
                        error_equals=["States.ALL"],
                        next="HandleParameterError"
                    )
                ]
            ),

            # Parse token JSON and calculate seconds until expiration using JSONata
            PassState(
                name="CalculateExpiration",
                output={
                    "seconds_until_expiration": "{% $parse($states.input.Parameter.Value).expires_at_epoch - ($millis() / 1000) %}"
                },
                next="PublishExpirationMetric"
            ),

            # Publish metric to CloudWatch
            TaskState(
                name="PublishExpirationMetric",
                resource="arn:aws:states:::aws-sdk:cloudwatch:putMetricData",
                arguments={
                    "Namespace": f"SESMail/{environment}",
                    "MetricData": [
                        {
                            "MetricName": "TokenSecondsUntilExpiration",
                            "Value": "{% $states.input.seconds_until_expiration %}",
                            "Unit": "Seconds"
                        }
                    ]
                },
                next="MonitoringComplete",
                catch=[
                    Catch(
                        error_equals=["States.ALL"],
                        next="HandleMetricPublishError"
                    )
                ]
            ),

            # Success state
            SucceedState(name="MonitoringComplete"),

            # Error handling: Missing parameter
            PassState(
                name="HandleMissingParameter",
                output={
                    "ErrorType": "ParameterNotFound",
                    "ErrorMessage": "SSM parameter does not exist - run refresh_oauth_token.py"
                },
                next="PublishErrorMetric"
            ),

            # Error handling: Parameter read error
            PassState(
                name="HandleParameterError",
                output={
                    "ErrorType": "ParameterReadError",
                    "ErrorMessage": "Failed to read SSM parameter"
                },
                next="PublishErrorMetric"
            ),

            # Error handling: Metric publish error
            PassState(
                name="HandleMetricPublishError",
                output={
                    "ErrorType": "MetricPublishError",
                    "ErrorMessage": "Failed to publish metric to CloudWatch"
                },
                next="PublishErrorMetric"
            ),

            # Publish error metric for monitoring system health
            TaskState(
                name="PublishErrorMetric",
                resource="arn:aws:states:::aws-sdk:cloudwatch:putMetricData",
                arguments={
                    "Namespace": f"SESMail/{environment}",
                    "MetricData": [
                        {
                            "MetricName": "TokenMonitoringErrors",
                            "Value": 1,
                            "Unit": "Count"
                        }
                    ]
                },
                next="MonitoringFailed",
                catch=[
                    Catch(
                        error_equals=["States.ALL"],
                        next="MonitoringFailed"
                    )
                ]
            ),

            # Terminal failure state
            FailState(
                name="MonitoringFailed",
                error="TokenMonitoringFailed",
                cause="Token monitoring workflow failed - check CloudWatch logs"
            )
        ]
    )
```

## Phase 3: YAML Generation Script

### File: `scripts/generate_stepfunctions.py`

```python
#!/usr/bin/env python3
"""
Generate Step Functions YAML files from Python DSL definitions.

This script is run automatically by the Makefile before Terraform operations
to ensure YAML files are always in sync with DSL definitions.
"""

import os
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from stepfunctions.token_monitor import create_token_monitor
# from stepfunctions.retry_processor import create_retry_processor  # Phase 3

def generate_yaml_files():
    """Generate YAML files for all state machines"""

    output_dir = project_root / "terraform/modules/ses-mail/stepfunctions"
    output_dir.mkdir(parents=True, exist_ok=True)

    print("Generating Step Functions YAML files...")

    # Token Monitor - uses template variables
    token_monitor = create_token_monitor(
        parameter_name="${parameter_name}",
        environment="${environment}"
    )
    token_monitor_path = output_dir / "token-monitor.yaml"
    token_monitor.to_yaml(str(token_monitor_path))
    print(f"  ✓ Generated {token_monitor_path}")

    # Retry Processor - TODO in Phase 3
    # retry_processor = create_retry_processor(
    #     queue_url="${queue_url}",
    #     lambda_arn="${lambda_arn}",
    #     environment="${environment}"
    # )
    # retry_processor_path = output_dir / "retry-processor.yaml"
    # retry_processor.to_yaml(str(retry_processor_path))
    # print(f"  ✓ Generated {retry_processor_path}")

    print("✅ All Step Functions YAML files generated successfully")

if __name__ == "__main__":
    generate_yaml_files()
```

## Phase 4: Test Infrastructure

### File: `tests/stepfunctions/helpers.py`

```python
"""Helper utilities for testing Step Functions with AWS TestState API"""

import json
import subprocess
from typing import Any, Dict, Optional
from pathlib import Path

def test_state_cli(
    definition: Dict[str, Any],
    input_data: Optional[Dict[str, Any]] = None,
    mock: Optional[Dict[str, Any]] = None,
    inspection_level: str = "INFO",
    role_arn: Optional[str] = None
) -> Dict[str, Any]:
    """
    Test a single state using AWS CLI test-state command.

    Args:
        definition: State definition in ASL format
        input_data: Input data for the state
        mock: Mock service response (optional)
        inspection_level: INFO, DEBUG, or TRACE
        role_arn: IAM role ARN (optional when using mocks)

    Returns:
        Test result dict with status, output, and inspection data
    """
    cmd = ["aws", "stepfunctions", "test-state"]

    # Add definition
    cmd.extend(["--definition", json.dumps(definition)])

    # Add input
    if input_data:
        cmd.extend(["--input", json.dumps(input_data)])

    # Add mock
    if mock:
        cmd.extend(["--mock", json.dumps(mock)])

    # Add inspection level
    cmd.extend(["--inspection-level", inspection_level])

    # Add role ARN if provided
    if role_arn:
        cmd.extend(["--role-arn", role_arn])

    # Run command
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=True
    )

    return json.loads(result.stdout)

def mock_ssm_get_parameter(value: str) -> Dict[str, Any]:
    """Generate mock for SSM GetParameter success"""
    return {
        "result": json.dumps({
            "Parameter": {
                "Name": "/test/parameter",
                "Type": "SecureString",
                "Value": value,
                "Version": 1
            }
        })
    }

def mock_ssm_parameter_not_found() -> Dict[str, Any]:
    """Generate mock for SSM ParameterNotFound error"""
    return {
        "errorOutput": {
            "error": "Ssm.ParameterNotFound",
            "cause": "Parameter not found"
        }
    }

def mock_cloudwatch_put_metric_success() -> Dict[str, Any]:
    """Generate mock for CloudWatch PutMetricData success"""
    return {
        "result": json.dumps({})
    }
```

### File: `tests/stepfunctions/test_token_monitor.py`

```python
"""Tests for token-monitor Step Function"""

import json
import pytest
from stepfunctions.token_monitor import create_token_monitor
from tests.stepfunctions.helpers import (
    test_state_cli,
    mock_ssm_get_parameter,
    mock_ssm_parameter_not_found,
    mock_cloudwatch_put_metric_success
)

class TestTokenMonitor:
    """Test suite for token monitor state machine"""

    @classmethod
    def setup_class(cls):
        """Setup: Create state machine definition"""
        cls.state_machine = create_token_monitor(
            parameter_name="/ses-mail/test/gmail-token",
            environment="test"
        )

    def test_get_token_parameter_success(self):
        """Test GetTokenParameter state with valid token"""
        state = self.state_machine.get_state("GetTokenParameter")

        # Mock SSM response with valid token
        token_data = {
            "expires_at_epoch": 1234567890,
            "refresh_token": "mock_token"
        }

        result = test_state_cli(
            definition=state.to_asl(),
            input_data={},
            mock=mock_ssm_get_parameter(json.dumps(token_data)),
            inspection_level="DEBUG"
        )

        assert result["status"] == "SUCCEEDED"
        assert "Parameter" in json.loads(result["output"])

    def test_get_token_parameter_not_found(self):
        """Test GetTokenParameter state when parameter missing"""
        state = self.state_machine.get_state("GetTokenParameter")

        result = test_state_cli(
            definition=state.to_asl(),
            input_data={},
            mock=mock_ssm_parameter_not_found(),
            inspection_level="DEBUG"
        )

        assert result["status"] == "CAUGHT_ERROR"
        assert result["nextState"] == "HandleMissingParameter"

    def test_calculate_expiration(self):
        """Test CalculateExpiration state JSONata transformation"""
        state = self.state_machine.get_state("CalculateExpiration")

        # Note: JSONata evaluation would require actual Step Functions
        # This test validates the state definition structure
        asl = state.to_asl()
        assert asl["Type"] == "Pass"
        assert "Output" in asl
        assert "seconds_until_expiration" in asl["Output"]

    def test_publish_expiration_metric(self):
        """Test PublishExpirationMetric state"""
        state = self.state_machine.get_state("PublishExpirationMetric")

        result = test_state_cli(
            definition=state.to_asl(),
            input_data={"seconds_until_expiration": 3600},
            mock=mock_cloudwatch_put_metric_success(),
            inspection_level="DEBUG"
        )

        assert result["status"] == "SUCCEEDED"
        assert result["nextState"] == "MonitoringComplete"

    def test_error_path_missing_parameter(self):
        """Test error handling for missing parameter"""
        state = self.state_machine.get_state("HandleMissingParameter")

        result = test_state_cli(
            definition=state.to_asl(),
            input_data={},
            inspection_level="DEBUG"
        )

        assert result["status"] == "SUCCEEDED"
        output = json.loads(result["output"])
        assert output["ErrorType"] == "ParameterNotFound"
        assert "run refresh_oauth_token.py" in output["ErrorMessage"]
```

## Phase 5: Makefile Integration

### File: `Makefile` (modifications)

```makefile
# Add new target to generate Step Functions YAML
.PHONY: generate-stepfunctions
generate-stepfunctions:
 @echo "Generating Step Functions YAML from DSL..."
 @python3 scripts/generate_stepfunctions.py

# Add new target to run Step Functions tests
.PHONY: test-stepfunctions
test-stepfunctions:
 @echo "Running Step Functions unit tests..."
 @AWS_PROFILE=$(AWS_PROFILE) pytest tests/stepfunctions/ -v

# Update validate to include Step Functions generation
validate: generate-stepfunctions
 @echo "Validating Terraform configuration..."
 # ... existing validation logic

# Update plan to generate YAML first
plan: generate-stepfunctions
 @echo "Creating Terraform plan..."
 # ... existing plan logic

# Update apply to generate YAML first
apply: generate-stepfunctions
 @echo "Applying Terraform changes..."
 # ... existing apply logic
```

## Phase 6: Dependencies

### File: `requirements.txt` (add)

```plain
# Existing dependencies
google-auth>=2.34.0
google-auth-oauthlib>=1.2.1
google-api-python-client>=2.141.0
boto3
aws_xray_sdk
aws-lambda-powertools>=3.6.0

# New testing dependencies
pytest>=7.4.0
PyYAML>=6.0.1
```

## Implementation Approach: Test-Driven Development

**IMPORTANT**: We will use the `superpowers:test-driven-development` skill to build the DSL library and tooling. This ensures the testing infrastructure itself is well-tested and reliable.

### TDD Workflow

For each component:

1. Write failing test first
2. Implement minimal code to pass test
3. Refactor
4. Repeat

### Component Order (TDD)

1. **DSL Core Classes** (Test → Implement)
   - Test: StateMachine.to_asl() produces correct dict
   - Test: StateMachine.to_yaml() writes valid YAML
   - Test: StateMachine.get_state() retrieves states
   - Implement: core.py

2. **State Types** (Test → Implement)
   - Test: TaskState.to_asl() with all parameters
   - Test: PassState with JSONata Output
   - Test: ChoiceState with rules
   - Test: SucceedState, FailState
   - Implement: states.py

3. **Error Handling** (Test → Implement)
   - Test: Retry.to_asl() format
   - Test: Catch.to_asl() format
   - Implement: error_handling.py

4. **Token Monitor Definition** (Test → Implement)
   - Test: create_token_monitor() generates correct ASL
   - Test: Generated YAML matches existing token-monitor.yaml
   - Implement: token_monitor.py

5. **YAML Generation Script** (Test → Implement)
   - Test: Script generates YAML files in correct location
   - Test: Generated YAML is valid and matches expected
   - Implement: generate_stepfunctions.py

6. **Test Helpers** (Test → Implement)
   - Test: test_state_cli() calls AWS CLI correctly
   - Test: Mock helpers generate correct format
   - Implement: helpers.py

7. **Token Monitor Tests** (Test → Implement)
   - Test: Each state behaves correctly with TestState API
   - Implement: test_token_monitor.py

## Migration Path

### Phase 1: Token Monitor (Using TDD)

1. ✅ Use `superpowers:test-driven-development` skill to build DSL library
2. ✅ Write tests for core.py, then implement
3. ✅ Write tests for states.py, then implement
4. ✅ Write tests for error_handling.py, then implement
5. ✅ Write tests for token_monitor.py, then implement
6. ✅ Write tests for generate_stepfunctions.py, then implement
7. ✅ Verify generated YAML matches existing with `git diff`
8. ✅ Write test helpers (helpers.py)
9. ✅ Write Step Functions tests (test_token_monitor.py)
10. ✅ Add Makefile targets
11. ✅ Update requirements.txt
12. ✅ Run `make test-stepfunctions` to validate

### Phase 2: Retry Processor (Week 2)

1. Add MapState support to DSL (already in states.py)
2. Define retry_processor.py in DSL
3. Update generate_stepfunctions.py
4. Generate YAML and verify
5. Write tests for retry processor
6. Validate all tests pass

### Phase 3: Documentation & Cleanup (Week 3)

1. Update CLAUDE.md with DSL workflow
2. Document contract derivation approach
3. Add examples to README
4. Consider moving generated YAML to .gitignore (after validation)
5. Create developer guide for adding new state machines

## Validation Strategy

### Before Each Commit

1. Run `make generate-stepfunctions`
2. Run `git diff terraform/modules/ses-mail/stepfunctions/`
3. Verify generated YAML matches hand-written (during migration)
4. Run `make test-stepfunctions` (all tests must pass)
5. Run `make plan ENV=test >/dev/null` (no Terraform changes)

### Regression Testing

- Keep existing integration_test.py running
- Pre-deployment unit tests catch logic bugs
- Post-deployment integration tests catch AWS integration issues

## Benefits

1. **Single Source of Truth**: Python DSL generates both YAML and contracts
2. **No Drift**: Impossible for YAML and tests to get out of sync
3. **Type Safety**: IDE autocomplete and type checking
4. **Pre-Deployment Validation**: Catch bugs before `make apply`
5. **Fast Feedback**: Tests run in seconds, no AWS deployment needed
6. **Extensible**: Easy to add new states and state machines
7. **Maintainable**: Change DSL once, YAML and tests update automatically

## Critical Files

**New files:**

- `stepfunctions_dsl/core.py` - DSL base classes
- `stepfunctions_dsl/states.py` - State implementations
- `stepfunctions_dsl/error_handling.py` - Retry/Catch
- `stepfunctions/token_monitor.py` - Token monitor definition
- `scripts/generate_stepfunctions.py` - YAML generator
- `tests/stepfunctions/helpers.py` - Test utilities
- `tests/stepfunctions/test_token_monitor.py` - Tests

**Modified files:**

- `Makefile` - Add generate + test targets
- `requirements.txt` - Add pytest, PyYAML

**Generated files (keep in git during migration):**

- `terraform/modules/ses-mail/stepfunctions/token-monitor.yaml`
- `terraform/modules/ses-mail/stepfunctions/retry-processor.yaml`

---

## Ancillary Information & Design Rationale

### Design Decisions

**Q: Why Python DSL instead of keeping YAML as source of truth?**
A: Single source of truth eliminates drift. YAML + separate contracts will inevitably diverge. Python provides type safety, IDE support, and can generate both YAML and derive contracts automatically.

**Q: Why not use AWS CDK for Step Functions?**
A: Project uses Terraform, not CDK. Mixing IaC tools adds complexity. A minimal custom DSL gives us exactly what we need without CDK overhead.

**Q: Why commit generated YAML to git?**
A: During migration, committed YAML allows us to verify generation is correct with `git diff`. After validation, we can move to .gitignore, but keeping it in git provides transparency and allows Terraform to work even if generation fails.

**Q: Why use pytest instead of extending existing integration_test.py?**
A: Different purposes - integration_test.py validates end-to-end AWS behavior after deployment. These are unit tests that run pre-deployment. pytest is standard for Python unit testing and provides better assertion capabilities than the custom framework.

**Q: Why TestState API instead of deploying and testing?**
A: TestState API allows testing state logic in isolation without deployment. Faster feedback (seconds vs minutes), no AWS resource costs, can test error paths safely, catches bugs before they hit AWS.

**Q: Why start with token-monitor instead of retry-processor?**
A: Token monitor is simpler (93 lines vs 180 lines, no Map state). Validates the DSL approach on easier problem first. Proves the concept before tackling complex Map state logic.

### AWS TestState API Key Capabilities

From <https://docs.aws.amazon.com/step-functions/latest/dg/test-state-isolation.html>:

**Inspection Levels:**

- `INFO` - Basic output/error (default)
- `DEBUG` - Shows data flow through InputPath, Parameters, ResultPath, OutputPath
- `TRACE` - HTTP request/response (HTTP Task only)

**Validation Modes:**

- `STRICT` - Enforces field naming, size, shape, type (default - use this)
- `PRESENT` - Validates only present fields
- `NONE` - Skips validation

**Mock Format:**

```python
# Success mock
{"result": "{\"key\": \"value\"}"}  # Note: value is JSON string

# Error mock
{"errorOutput": {"error": "ServiceException", "cause": "Details"}}
```

**What Can Be Tested:**

- ✅ Data transformations (InputPath, Parameters, ResultPath, OutputPath)
- ✅ Error handling (Catch/Retry blocks)
- ✅ Map state logic (requires mocking iterator results)
- ✅ Parallel state logic (requires mocking branch results)
- ✅ Choice conditions
- ✅ Context field values
- ✅ Retry backoff calculations

**What Cannot Be Tested:**

- ❌ Cross-state execution flow (can chain tests manually)
- ❌ JSONata expression evaluation (requires actual Step Functions runtime)
- ❌ Real AWS service behavior (that's what integration tests are for)

### Current Step Functions

**token-monitor.yaml** (93 lines):

- Uses JSONata query language
- States: GetTokenParameter, CalculateExpiration, PublishExpirationMetric, MonitoringComplete, HandleMissingParameter, HandleParameterError, HandleMetricPublishError, PublishErrorMetric, MonitoringFailed
- AWS integrations: SSM GetParameter, CloudWatch PutMetricData
- Error handling: 3 error paths with catch blocks
- Terraform variables: `${parameter_name}`, `${environment}`

**retry-processor.yaml** (180 lines):

- Uses standard JSONPath
- States: ReadMessagesFromQueue, CheckIfMessagesExist, NoMessagesToProcess, ProcessMessages (Map), ParseMessageBody, InvokeGmailForwarder, DeleteMessageFromQueue, MessageProcessedSuccessfully, InvocationFailed, DeleteFailedMessage, MessageProcessingComplete, CheckForMoreMessages, AllMessagesProcessed, PublishCompletionMetrics, MetricsPublishFailed, RetryProcessingComplete, HandleQueueReadError, QueueReadFailed
- AWS integrations: SQS ReceiveMessage, SQS DeleteMessage, Lambda Invoke, CloudWatch PutMetricData
- Error handling: Multiple retry blocks with exponential backoff, catch blocks at multiple levels
- Map state: Processes up to 10 messages with MaxConcurrency=1
- Terraform variables: `${queue_url}`, `${lambda_arn}`, `${environment}`

### Terraform Integration Points

State machines are defined in `stepfunctions.tf`:

```hcl
resource "aws_sfn_state_machine" "token_monitor" {
  name     = "ses-mail-gmail-token-monitor-${var.environment}"
  role_arn = aws_iam_role.stepfunction_token_monitor.arn

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.stepfunction_token_monitor_logs.arn}:*"
    include_execution_data = true
    level                  = "ERROR"
  }

  definition = jsonencode(yamldecode(templatefile(
    "${path.module}/stepfunctions/token-monitor.yaml",
    {
      parameter_name = aws_ssm_parameter.gmail_oauth_refresh_token.name
      environment    = var.environment
    }
  )))
}
```

**Key points:**

- Uses `templatefile()` to inject Terraform variables
- Wraps with `yamldecode()` then `jsonencode()` (YAML → JSON)
- Generated YAML must preserve `${variable}` placeholders

### Future Enhancements

**Phase 4: Advanced Features** (Optional, post-MVP)

1. Contract validation with Pydantic models
2. Mock fixture generation from contracts
3. State machine visualization (generate diagrams from DSL)
4. ASL linting (validate state machine structure)
5. Parameter extraction (automatically detect required Terraform variables)
6. State machine composition (reusable sub-workflows)

**Integration with Existing Workflows:**

- Add to `.kiro/specs/` if formalized into a project
- Update CLAUDE.md with DSL workflow documentation
- Create developer guide: "How to Add a New Step Function"

### Testing Strategy

**Pre-deployment (New - TestState API):**

- Unit test individual states in isolation
- Mock AWS service responses
- Validate data transformations
- Test error handling paths
- Fast feedback (seconds)

**Post-deployment (Existing - integration_test.py):**

- End-to-end pipeline validation
- Real AWS service integrations
- X-Ray trace verification
- Queue and DLQ monitoring
- Comprehensive but slower (minutes)

**Both are necessary:**

- Unit tests catch logic bugs early
- Integration tests catch AWS configuration issues
- Together provide comprehensive coverage

### Questions Resolved During Planning

1. ✅ **Testing goal**: Pre-deployment validation
2. ✅ **Test framework**: Python (pytest) with Makefile target
3. ✅ **Test coverage**: All aspects (data, errors, control flow, integrations)
4. ✅ **Test organization**: One file per state machine
5. ✅ **Single source of truth**: Python DSL generates both YAML and contracts
6. ✅ **Implementation approach**: Test-driven development (TDD)

### Dependencies

**Python packages required:**

- `pytest>=7.4.0` - Testing framework
- `PyYAML>=6.0.1` - YAML generation
- `boto3` - Already present (AWS SDK)

**AWS CLI requirements:**

- AWS CLI v2 with Step Functions support
- `aws stepfunctions test-state` command available
- `AWS_PROFILE=ses-mail` configured with permissions:
  - `states:TestState` (for running tests)
  - `iam:PassRole` (optional if using mocks only)

**System requirements:**

- Python 3.13 (current project version)
- Git (for verifying generated YAML)

### Risk Mitigation

#### Risk: Generated YAML differs from hand-written

- Mitigation: Keep generated YAML in git during migration
- Validation: `git diff` after generation, Terraform plan should show no changes
- Recovery: Revert to manual YAML if needed

#### Risk: JSONata expressions not testable with TestState

- Mitigation: Test state structure, not JSONata evaluation
- Acceptance: Integration tests validate JSONata behavior
- Alternative: Document known limitation

#### Risk: DSL becomes too complex

- Mitigation: Start minimal, add features only as needed
- YAGNI principle: Don't add state types we don't use
- Review: Reassess after token-monitor migration

#### Risk: Learning curve for team

- Mitigation: Comprehensive examples in plan
- Documentation: Clear developer guide
- Migration: Start with one state machine, prove value

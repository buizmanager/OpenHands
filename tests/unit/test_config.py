import logging
import os
from io import StringIO

import pytest

from openhands.core.config import (
    AgentConfig,
    AppConfig,
    LLMConfig,
    finalize_config,
    get_agent_config_arg,
    get_llm_config_arg,
    load_app_config,
    load_from_env,
    load_from_toml,
)
from openhands.core.config.condenser_config import (
    NoOpCondenserConfig,
)
from openhands.core.logger import openhands_logger


@pytest.fixture
def setup_env():
    # Create old-style and new-style TOML files
    with open('old_style_config.toml', 'w') as f:
        f.write('[default]\nLLM_MODEL="GPT-4"\n')

    with open('new_style_config.toml', 'w') as f:
        f.write('[app]\nLLM_MODEL="GPT-3"\n')

    yield

    # Cleanup TOML files after the test
    os.remove('old_style_config.toml')
    os.remove('new_style_config.toml')


@pytest.fixture
def temp_toml_file(tmp_path):
    # Fixture to create a temporary directory and TOML file for testing
    tmp_toml_file = os.path.join(tmp_path, 'config.toml')
    yield tmp_toml_file


@pytest.fixture
def default_config(monkeypatch):
    # Fixture to provide a default AppConfig instance
    yield AppConfig()


def test_compat_env_to_config(monkeypatch, setup_env):
    # Use `monkeypatch` to set environment variables for this specific test
    monkeypatch.setenv('WORKSPACE_BASE', '/repos/openhands/workspace')
    monkeypatch.setenv('LLM_API_KEY', 'sk-proj-rgMV0...')
    monkeypatch.setenv('LLM_MODEL', 'gpt-4o')
    monkeypatch.setenv('AGENT_MEMORY_MAX_THREADS', '4')
    monkeypatch.setenv('AGENT_MEMORY_ENABLED', 'True')
    monkeypatch.setenv('DEFAULT_AGENT', 'CodeActAgent')
    monkeypatch.setenv('SANDBOX_TIMEOUT', '10')

    config = AppConfig()
    load_from_env(config, os.environ)

    assert config.workspace_base == '/repos/openhands/workspace'
    assert isinstance(config.get_llm_config(), LLMConfig)
    assert config.get_llm_config().api_key.get_secret_value() == 'sk-proj-rgMV0...'
    assert config.get_llm_config().model == 'gpt-4o'
    assert isinstance(config.get_agent_config(), AgentConfig)
    assert isinstance(config.get_agent_config().memory_max_threads, int)
    assert config.get_agent_config().memory_max_threads == 4
    assert config.get_agent_config().memory_enabled is True
    assert config.default_agent == 'CodeActAgent'
    assert config.sandbox.timeout == 10


def test_load_from_old_style_env(monkeypatch, default_config):
    # Test loading configuration from old-style environment variables using monkeypatch
    monkeypatch.setenv('LLM_API_KEY', 'test-api-key')
    monkeypatch.setenv('AGENT_MEMORY_ENABLED', 'True')
    monkeypatch.setenv('DEFAULT_AGENT', 'BrowsingAgent')
    monkeypatch.setenv('WORKSPACE_BASE', '/opt/files/workspace')
    monkeypatch.setenv('SANDBOX_BASE_CONTAINER_IMAGE', 'custom_image')

    load_from_env(default_config, os.environ)

    assert default_config.get_llm_config().api_key.get_secret_value() == 'test-api-key'
    assert default_config.get_agent_config().memory_enabled is True
    assert default_config.default_agent == 'BrowsingAgent'
    assert default_config.workspace_base == '/opt/files/workspace'
    assert default_config.workspace_mount_path is None  # before finalize_config
    assert default_config.workspace_mount_path_in_sandbox is not None
    assert default_config.sandbox.base_container_image == 'custom_image'


def test_load_from_new_style_toml(default_config, temp_toml_file):
    # Test loading configuration from a new-style TOML file
    with open(temp_toml_file, 'w', encoding='utf-8') as toml_file:
        toml_file.write(
            """
[llm]
model = "test-model"
api_key = "toml-api-key"

[llm.cheap]
model = "some-cheap-model"
api_key = "cheap-model-api-key"

[agent]
memory_enabled = true

[agent.BrowsingAgent]
llm_config = "cheap"
memory_enabled = false

[sandbox]
timeout = 1

[core]
workspace_base = "/opt/files2/workspace"
default_agent = "TestAgent"
"""
        )

    load_from_toml(default_config, temp_toml_file)

    # default llm & agent configs
    assert default_config.default_agent == 'TestAgent'
    assert default_config.get_llm_config().model == 'test-model'
    assert default_config.get_llm_config().api_key.get_secret_value() == 'toml-api-key'
    assert default_config.get_agent_config().memory_enabled is True

    # undefined agent config inherits default ones
    assert (
        default_config.get_llm_config_from_agent('CodeActAgent')
        == default_config.get_llm_config()
    )
    assert default_config.get_agent_config('CodeActAgent').memory_enabled is True

    # defined agent config overrides default ones
    assert default_config.get_llm_config_from_agent(
        'BrowsingAgent'
    ) == default_config.get_llm_config('cheap')
    assert (
        default_config.get_llm_config_from_agent('BrowsingAgent').model
        == 'some-cheap-model'
    )
    assert default_config.get_agent_config('BrowsingAgent').memory_enabled is False

    assert default_config.workspace_base == '/opt/files2/workspace'
    assert default_config.sandbox.timeout == 1

    assert default_config.workspace_mount_path is None
    assert default_config.workspace_mount_path_in_sandbox is not None
    assert default_config.workspace_mount_path_in_sandbox == '/workspace'

    finalize_config(default_config)

    # after finalize_config, workspace_mount_path is set to the absolute path of workspace_base
    # if it was undefined
    assert default_config.workspace_mount_path == '/opt/files2/workspace'


def test_llm_config_native_tool_calling(default_config, temp_toml_file, monkeypatch):
    # default is None
    assert default_config.get_llm_config().native_tool_calling is None

    # set to false
    with open(temp_toml_file, 'w', encoding='utf-8') as toml_file:
        toml_file.write(
            """
[core]

[llm.gpt4o-mini]
native_tool_calling = false
"""
        )
    load_from_toml(default_config, temp_toml_file)
    assert default_config.get_llm_config().native_tool_calling is None
    assert default_config.get_llm_config('gpt4o-mini').native_tool_calling is False

    # set to true using string
    with open(temp_toml_file, 'w', encoding='utf-8') as toml_file:
        toml_file.write(
            """
[core]

[llm.gpt4o-mini]
native_tool_calling = true
"""
        )
    load_from_toml(default_config, temp_toml_file)
    assert default_config.get_llm_config('gpt4o-mini').native_tool_calling is True

    # override to false by env
    # see utils.set_attr_from_env
    monkeypatch.setenv('LLM_NATIVE_TOOL_CALLING', 'false')
    load_from_env(default_config, os.environ)
    assert default_config.get_llm_config().native_tool_calling is False
    assert (
        default_config.get_llm_config('gpt4o-mini').native_tool_calling is True
    )  # load_from_env didn't override the named config set in the toml file under [llm.gpt4o-mini]


def test_env_overrides_compat_toml(monkeypatch, default_config, temp_toml_file):
    # test that environment variables override TOML values using monkeypatch
    # uses a toml file with sandbox_vars instead of a sandbox section
    with open(temp_toml_file, 'w', encoding='utf-8') as toml_file:
        toml_file.write("""
[llm]
model = "test-model"
api_key = "toml-api-key"

[core]
workspace_base = "/opt/files3/workspace"
disable_color = true
sandbox_timeout = 500
sandbox_user_id = 1001
""")

    monkeypatch.setenv('LLM_API_KEY', 'env-api-key')
    monkeypatch.setenv('WORKSPACE_BASE', 'UNDEFINED')
    monkeypatch.setenv('SANDBOX_TIMEOUT', '1000')
    monkeypatch.setenv('SANDBOX_USER_ID', '1002')
    monkeypatch.delenv('LLM_MODEL', raising=False)

    load_from_toml(default_config, temp_toml_file)

    assert default_config.workspace_mount_path is None

    load_from_env(default_config, os.environ)

    assert os.environ.get('LLM_MODEL') is None
    assert default_config.get_llm_config().model == 'test-model'
    assert default_config.get_llm_config('llm').model == 'test-model'
    assert default_config.get_llm_config_from_agent().model == 'test-model'
    assert default_config.get_llm_config().api_key.get_secret_value() == 'env-api-key'

    # after we set workspace_base to 'UNDEFINED' in the environment,
    # workspace_base should be set to that
    assert default_config.workspace_base is not None
    assert default_config.workspace_base == 'UNDEFINED'
    assert default_config.workspace_mount_path is None

    assert default_config.disable_color is True
    assert default_config.sandbox.timeout == 1000
    assert default_config.sandbox.user_id == 1002

    finalize_config(default_config)
    # after finalize_config, workspace_mount_path is set to absolute path of workspace_base if it was undefined
    assert default_config.workspace_mount_path == os.getcwd() + '/UNDEFINED'


def test_env_overrides_sandbox_toml(monkeypatch, default_config, temp_toml_file):
    # test that environment variables override TOML values using monkeypatch
    # uses a toml file with a sandbox section
    with open(temp_toml_file, 'w', encoding='utf-8') as toml_file:
        toml_file.write("""
[llm]
model = "test-model"
api_key = "toml-api-key"

[core]
workspace_base = "/opt/files3/workspace"

[sandbox]
timeout = 500
user_id = 1001
""")

    monkeypatch.setenv('LLM_API_KEY', 'env-api-key')
    monkeypatch.setenv('WORKSPACE_BASE', 'UNDEFINED')
    monkeypatch.setenv('SANDBOX_TIMEOUT', '1000')
    monkeypatch.setenv('SANDBOX_USER_ID', '1002')
    monkeypatch.delenv('LLM_MODEL', raising=False)

    load_from_toml(default_config, temp_toml_file)

    assert default_config.workspace_mount_path is None

    # before load_from_env, values are set to the values from the toml file
    assert default_config.get_llm_config().api_key.get_secret_value() == 'toml-api-key'
    assert default_config.sandbox.timeout == 500
    assert default_config.sandbox.user_id == 1001

    load_from_env(default_config, os.environ)

    # values from env override values from toml
    assert os.environ.get('LLM_MODEL') is None
    assert default_config.get_llm_config().model == 'test-model'
    assert default_config.get_llm_config().api_key.get_secret_value() == 'env-api-key'

    assert default_config.sandbox.timeout == 1000
    assert default_config.sandbox.user_id == 1002

    finalize_config(default_config)
    # after finalize_config, workspace_mount_path is set to absolute path of workspace_base if it was undefined
    assert default_config.workspace_mount_path == os.getcwd() + '/UNDEFINED'


def test_sandbox_config_from_toml(monkeypatch, default_config, temp_toml_file):
    # Test loading configuration from a new-style TOML file
    with open(temp_toml_file, 'w', encoding='utf-8') as toml_file:
        toml_file.write(
            """
[core]
workspace_base = "/opt/files/workspace"

[llm]
model = "test-model"

[sandbox]
timeout = 1
base_container_image = "custom_image"
user_id = 1001
"""
        )
    monkeypatch.setattr(os, 'environ', {})
    load_from_toml(default_config, temp_toml_file)
    load_from_env(default_config, os.environ)
    finalize_config(default_config)

    assert default_config.get_llm_config().model == 'test-model'
    assert default_config.sandbox.timeout == 1
    assert default_config.sandbox.base_container_image == 'custom_image'
    assert default_config.sandbox.user_id == 1001


def test_security_config_from_toml(default_config, temp_toml_file):
    """Test loading security specific configurations."""
    with open(temp_toml_file, 'w', encoding='utf-8') as toml_file:
        toml_file.write(
            """
[core]  # make sure core is loaded first
workspace_base = "/opt/files/workspace"

[llm]
model = "test-model"

[security]
confirmation_mode = false
security_analyzer = "semgrep"
"""
        )

    load_from_toml(default_config, temp_toml_file)
    assert default_config.security.confirmation_mode is False
    assert default_config.security.security_analyzer == 'semgrep'


def test_security_config_from_dict():
    """Test creating SecurityConfig instance from dictionary."""
    from openhands.core.config.security_config import SecurityConfig

    # Test with all fields
    config_dict = {'confirmation_mode': True, 'security_analyzer': 'some_analyzer'}

    security_config = SecurityConfig(**config_dict)

    # Verify all fields are correctly set
    assert security_config.confirmation_mode is True
    assert security_config.security_analyzer == 'some_analyzer'


def test_defaults_dict_after_updates(default_config):
    # Test that `defaults_dict` retains initial values after updates.
    initial_defaults = default_config.defaults_dict
    assert initial_defaults['workspace_mount_path']['default'] is None
    assert initial_defaults['default_agent']['default'] == 'CodeActAgent'

    updated_config = AppConfig()
    updated_config.get_llm_config().api_key = 'updated-api-key'
    updated_config.get_llm_config('llm').api_key = 'updated-api-key'
    updated_config.get_llm_config_from_agent('agent').api_key = 'updated-api-key'
    updated_config.get_llm_config_from_agent(
        'BrowsingAgent'
    ).api_key = 'updated-api-key'
    updated_config.default_agent = 'BrowsingAgent'

    defaults_after_updates = updated_config.defaults_dict
    assert defaults_after_updates['default_agent']['default'] == 'CodeActAgent'
    assert defaults_after_updates['workspace_mount_path']['default'] is None
    assert defaults_after_updates['sandbox']['timeout']['default'] == 120
    assert (
        defaults_after_updates['sandbox']['base_container_image']['default']
        == 'nikolaik/python-nodejs:python3.12-nodejs22'
    )
    assert defaults_after_updates == initial_defaults


def test_invalid_toml_format(monkeypatch, temp_toml_file, default_config):
    # Invalid TOML format doesn't break the configuration
    monkeypatch.setenv('LLM_MODEL', 'gpt-5-turbo-1106')
    monkeypatch.setenv('WORKSPACE_MOUNT_PATH', '/home/user/project')
    monkeypatch.delenv('LLM_API_KEY', raising=False)

    with open(temp_toml_file, 'w', encoding='utf-8') as toml_file:
        toml_file.write('INVALID TOML CONTENT')

    load_from_toml(default_config, temp_toml_file)
    load_from_env(default_config, os.environ)
    default_config.jwt_secret = None  # prevent leak
    for llm in default_config.llms.values():
        llm.api_key = None  # prevent leak
    assert default_config.get_llm_config().model == 'gpt-5-turbo-1106'
    assert default_config.get_llm_config().custom_llm_provider is None
    assert default_config.workspace_mount_path == '/home/user/project'


def test_load_from_toml_file_not_found(default_config):
    """Test loading configuration when the TOML file doesn't exist.

    This ensures that:
    1. The program doesn't crash when the config file is missing
    2. The config object retains its default values
    3. The application remains usable
    """
    # Try to load from a non-existent file
    load_from_toml(default_config, 'nonexistent.toml')

    # Verify that config object maintains default values
    assert default_config.get_llm_config() is not None
    assert default_config.get_agent_config() is not None
    assert default_config.sandbox is not None


def test_core_not_in_toml(default_config, temp_toml_file):
    """Test loading configuration when the core section is not in the TOML file.

    default values should be used for the missing sections.
    """
    with open(temp_toml_file, 'w', encoding='utf-8') as toml_file:
        toml_file.write("""
[llm]
model = "test-model"

[agent]
memory_enabled = true

[sandbox]
timeout = 1
base_container_image = "custom_image"
user_id = 1001

[security]
security_analyzer = "semgrep"
""")

    load_from_toml(default_config, temp_toml_file)
    assert default_config.get_llm_config().model == 'test-model'
    assert default_config.get_agent_config().memory_enabled is True
    assert default_config.sandbox.base_container_image == 'custom_image'
    assert default_config.sandbox.user_id == 1001
    assert default_config.security.security_analyzer == 'semgrep'


def test_load_from_toml_partial_invalid(default_config, temp_toml_file, caplog):
    """Test loading configuration with partially invalid TOML content.

    This ensures that:
    1. Valid configuration sections are properly loaded
    2. Invalid fields are ignored gracefully
    3. The config object maintains correct values for valid fields
    4. Appropriate warnings are logged for invalid fields

    See `openhands/core/schema/config.py` for the list of valid fields.
    """
    with open(temp_toml_file, 'w', encoding='utf-8') as f:
        f.write("""
[core]
debug = true

[llm]
# No set in `openhands/core/schema/config.py`
invalid_field = "test"
model = "gpt-4"

[agent]
memory_enabled = true

[sandbox]
invalid_field_in_sandbox = "test"
""")

    # Create a string buffer to capture log output
    # Referenced from test_logging.py and `mock_logger`
    log_output = StringIO()
    handler = logging.StreamHandler(log_output)
    handler.setLevel(logging.WARNING)
    formatter = logging.Formatter('%(message)s')
    handler.setFormatter(formatter)
    openhands_logger.addHandler(handler)

    try:
        load_from_toml(default_config, temp_toml_file)
        log_content = log_output.getvalue()

        # invalid [llm] config
        # Verify that the appropriate warning was logged
        assert 'Cannot parse [llm] config from toml' in log_content
        assert 'values have not been applied' in log_content
        # Error: LLMConfig.__init__() got an unexpected keyword argume
        assert 'Cannot parse [llm] config from toml' in log_content
        assert 'invalid_field' in log_content

        # invalid [sandbox] config
        assert 'Cannot parse [sandbox] config from toml' in log_content
        assert 'values have not been applied' in log_content
        assert 'invalid_field_in_sandbox' in log_content

        # Verify valid configurations are loaded. Load from default instead of `config.toml`
        # assert default_config.debug is True
        assert default_config.debug is True
        assert default_config.get_llm_config().model == 'claude-3-5-sonnet-20241022'
        assert default_config.get_agent_config().memory_enabled is True
    finally:
        openhands_logger.removeHandler(handler)


def test_finalize_config(default_config):
    # Test finalize config
    assert default_config.workspace_mount_path is None
    default_config.workspace_base = None
    finalize_config(default_config)

    assert default_config.workspace_mount_path is None


def test_workspace_mount_path_default(default_config):
    assert default_config.workspace_mount_path is None
    default_config.workspace_base = '/home/user/project'
    finalize_config(default_config)
    assert default_config.workspace_mount_path == os.path.abspath(
        default_config.workspace_base
    )


def test_workspace_mount_rewrite(default_config, monkeypatch):
    default_config.workspace_base = '/home/user/project'
    default_config.workspace_mount_rewrite = '/home/user:/sandbox'
    monkeypatch.setattr('os.getcwd', lambda: '/current/working/directory')
    finalize_config(default_config)
    assert default_config.workspace_mount_path == '/sandbox/project'


def test_embedding_base_url_default(default_config):
    default_config.get_llm_config().base_url = 'https://api.exampleapi.com'
    finalize_config(default_config)
    assert (
        default_config.get_llm_config().embedding_base_url
        == 'https://api.exampleapi.com'
    )


def test_cache_dir_creation(default_config, tmpdir):
    default_config.cache_dir = str(tmpdir.join('test_cache'))
    finalize_config(default_config)
    assert os.path.exists(default_config.cache_dir)


def test_agent_config_condenser_default():
    """Test that default agent condenser is NoOpCondenser."""
    config = AppConfig()
    agent_config = config.get_agent_config()
    assert isinstance(agent_config.condenser, NoOpCondenserConfig)


def test_api_keys_repr_str():
    # Test LLMConfig
    llm_config = LLMConfig(
        api_key='my_api_key',
        aws_access_key_id='my_access_key',
        aws_secret_access_key='my_secret_key',
    )

    # Check that no secret keys are emitted in representations of the config object
    assert 'my_api_key' not in repr(llm_config)
    assert 'my_api_key' not in str(llm_config)
    assert 'my_access_key' not in repr(llm_config)
    assert 'my_access_key' not in str(llm_config)
    assert 'my_secret_key' not in repr(llm_config)
    assert 'my_secret_key' not in str(llm_config)

    # Check that no other attrs in LLMConfig have 'key' or 'token' in their name
    # This will fail when new attrs are added, and attract attention
    known_key_token_attrs_llm = [
        'api_key',
        'aws_access_key_id',
        'aws_secret_access_key',
        'input_cost_per_token',
        'output_cost_per_token',
        'custom_tokenizer',
    ]
    for attr_name in LLMConfig.model_fields.keys():
        if (
            not attr_name.startswith('__')
            and attr_name not in known_key_token_attrs_llm
        ):
            assert (
                'key' not in attr_name.lower()
            ), f"Unexpected attribute '{attr_name}' contains 'key' in LLMConfig"
            assert (
                'token' not in attr_name.lower() or 'tokens' in attr_name.lower()
            ), f"Unexpected attribute '{attr_name}' contains 'token' in LLMConfig"

    # Test AgentConfig
    # No attrs in AgentConfig have 'key' or 'token' in their name
    agent_config = AgentConfig(memory_enabled=True, memory_max_threads=4)
    for attr_name in AgentConfig.model_fields.keys():
        if not attr_name.startswith('__'):
            assert (
                'key' not in attr_name.lower()
            ), f"Unexpected attribute '{attr_name}' contains 'key' in AgentConfig"
            assert (
                'token' not in attr_name.lower() or 'tokens' in attr_name.lower()
            ), f"Unexpected attribute '{attr_name}' contains 'token' in AgentConfig"

    # Test AppConfig
    app_config = AppConfig(
        llms={'llm': llm_config},
        agents={'agent': agent_config},
        e2b_api_key='my_e2b_api_key',
        jwt_secret='my_jwt_secret',
        modal_api_token_id='my_modal_api_token_id',
        modal_api_token_secret='my_modal_api_token_secret',
        runloop_api_key='my_runloop_api_key',
        daytona_api_key='my_daytona_api_key',
    )
    assert 'my_e2b_api_key' not in repr(app_config)
    assert 'my_e2b_api_key' not in str(app_config)
    assert 'my_jwt_secret' not in repr(app_config)
    assert 'my_jwt_secret' not in str(app_config)
    assert 'my_modal_api_token_id' not in repr(app_config)
    assert 'my_modal_api_token_id' not in str(app_config)
    assert 'my_modal_api_token_secret' not in repr(app_config)
    assert 'my_modal_api_token_secret' not in str(app_config)
    assert 'my_runloop_api_key' not in repr(app_config)
    assert 'my_runloop_api_key' not in str(app_config)
    assert 'my_daytona_api_key' not in repr(app_config)
    assert 'my_daytona_api_key' not in str(app_config)

    # Check that no other attrs in AppConfig have 'key' or 'token' in their name
    # This will fail when new attrs are added, and attract attention
    known_key_token_attrs_app = [
        'e2b_api_key',
        'modal_api_token_id',
        'modal_api_token_secret',
        'runloop_api_key',
        'daytona_api_key',
    ]
    for attr_name in AppConfig.model_fields.keys():
        if (
            not attr_name.startswith('__')
            and attr_name not in known_key_token_attrs_app
        ):
            assert (
                'key' not in attr_name.lower()
            ), f"Unexpected attribute '{attr_name}' contains 'key' in AppConfig"
            assert (
                'token' not in attr_name.lower() or 'tokens' in attr_name.lower()
            ), f"Unexpected attribute '{attr_name}' contains 'token' in AppConfig"


def test_max_iterations_and_max_budget_per_task_from_toml(temp_toml_file):
    temp_toml = """
[core]
max_iterations = 42
max_budget_per_task = 4.7
"""

    config = AppConfig()
    with open(temp_toml_file, 'w') as f:
        f.write(temp_toml)

    load_from_toml(config, temp_toml_file)

    assert config.max_iterations == 42
    assert config.max_budget_per_task == 4.7


def test_get_llm_config_arg(temp_toml_file):
    temp_toml = """
[core]
max_iterations = 100
max_budget_per_task = 4.0

[llm.gpt3]
model="gpt-3.5-turbo"
api_key="redacted"
embedding_model="openai"

[llm.gpt4o]
model="gpt-4o"
api_key="redacted"
embedding_model="openai"
"""

    with open(temp_toml_file, 'w') as f:
        f.write(temp_toml)

    llm_config = get_llm_config_arg('gpt3', temp_toml_file)
    assert llm_config.model == 'gpt-3.5-turbo'
    assert llm_config.embedding_model == 'openai'


def test_get_agent_configs(default_config, temp_toml_file):
    temp_toml = """
[core]
max_iterations = 100
max_budget_per_task = 4.0

[agent.CodeActAgent]
memory_enabled = true

[agent.BrowsingAgent]
memory_max_threads = 10
"""

    with open(temp_toml_file, 'w') as f:
        f.write(temp_toml)

    load_from_toml(default_config, temp_toml_file)

    codeact_config = default_config.get_agent_configs().get('CodeActAgent')
    assert codeact_config.memory_enabled is True
    browsing_config = default_config.get_agent_configs().get('BrowsingAgent')
    assert browsing_config.memory_max_threads == 10


def test_get_agent_config_arg(temp_toml_file):
    temp_toml = """
[core]
max_iterations = 100
max_budget_per_task = 4.0

[agent.CodeActAgent]
memory_enabled = true
enable_prompt_extensions = false

[agent.BrowsingAgent]
memory_enabled = false
enable_prompt_extensions = true
memory_max_threads = 10
"""

    with open(temp_toml_file, 'w') as f:
        f.write(temp_toml)

    agent_config = get_agent_config_arg('CodeActAgent', temp_toml_file)
    assert agent_config.memory_enabled
    assert not agent_config.enable_prompt_extensions

    agent_config2 = get_agent_config_arg('BrowsingAgent', temp_toml_file)
    assert not agent_config2.memory_enabled
    assert agent_config2.enable_prompt_extensions
    assert agent_config2.memory_max_threads == 10


def test_agent_config_custom_group_name(temp_toml_file):
    temp_toml = """
[core]
max_iterations = 99

[agent.group1]
memory_enabled = true

[agent.group2]
memory_enabled = false
"""
    with open(temp_toml_file, 'w') as f:
        f.write(temp_toml)

    # just a sanity check that load app config wouldn't fail
    app_config = load_app_config(config_file=temp_toml_file)
    assert app_config.max_iterations == 99

    # run_infer in evaluation can use `get_agent_config_arg` to load custom
    # agent configs with any group name (not just agent name)
    agent_config1 = get_agent_config_arg('group1', temp_toml_file)
    assert agent_config1.memory_enabled
    agent_config2 = get_agent_config_arg('group2', temp_toml_file)
    assert not agent_config2.memory_enabled

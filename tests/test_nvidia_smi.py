from app.telemetry.nvidia_smi import (
    GpuReading,
    NvidiaSmiParse,
    parse_query_output,
    parse_value,
    query_args,
)


def test_query_args():
    assert query_args() == [
        "--query-gpu=index,name,temperature.gpu,clocks.sm,clocks.mem,power.draw,utilization.gpu",
        "--format=csv,noheader,nounits",
    ]


def test_parse_real_rtx_5090_line():
    result = parse_query_output(
        "0, NVIDIA GeForce RTX 5090 Laptop GPU, 43, 240, 405, 10.50, 0"
    )

    assert result.error is None
    assert len(result.gpus) == 1

    gpu = result.gpus[0]
    assert isinstance(gpu, GpuReading)
    assert gpu.index == 0
    assert gpu.name == "NVIDIA GeForce RTX 5090 Laptop GPU"
    assert gpu.temperature_c == 43.0
    assert gpu.sm_clock_mhz == 240.0
    assert gpu.mem_clock_mhz == 405.0
    assert gpu.power_w == 10.5
    assert gpu.utilization_pct == 0.0


def test_parse_two_gpus():
    text = (
        "0, NVIDIA GeForce RTX 5090 Laptop GPU, 43, 240, 405, 10.50, 0\n"
        "1, NVIDIA GeForce RTX 5090 Laptop GPU, 44, 241, 406, 11.25, 1"
    )

    result = parse_query_output(text)

    assert result.error is None
    assert [gpu.index for gpu in result.gpus] == [0, 1]
    assert result.gpus[1].temperature_c == 44.0
    assert result.gpus[1].utilization_pct == 1.0


def test_na_power_is_none_not_zero():
    result = parse_query_output("0, GPU, 43, 240, 405, [N/A], 0")

    assert result.error is None
    assert result.gpus[0].power_w is None
    assert result.gpus[0].utilization_pct == 0.0


def test_not_supported_clock_is_none():
    result = parse_query_output("0, GPU, 43, [Not Supported], 405, 10.50, 0")

    assert result.error is None
    assert result.gpus[0].sm_clock_mhz is None
    assert result.gpus[0].mem_clock_mhz == 405.0


def test_wrong_field_count_line_skipped_when_valid_line_exists():
    text = "bad line\n0, GPU, 43, 240, 405, 10.50, 0"

    result = parse_query_output(text)

    assert result.error is None
    assert len(result.gpus) == 1
    assert result.gpus[0].index == 0


def test_only_bad_lines_returns_error():
    result = parse_query_output("bad line\nanother bad line")

    assert result.gpus == []
    assert result.error == "unexpected line: bad line"          # the first bad line


def test_empty_text_returns_no_output_error():
    result = parse_query_output("")

    assert result.gpus == []
    assert result.error == "no output from nvidia-smi"


def test_whitespace_only_returns_no_output_error():
    result = parse_query_output("   \n\t  ")

    assert result.gpus == []
    assert result.error == "no output from nvidia-smi"


def test_non_str_input_returns_no_output_error():
    result = parse_query_output(None)

    assert result.gpus == []
    assert result.error == "no output from nvidia-smi"


def test_nvidia_smi_failed_line_returns_error():
    line = "NVIDIA-SMI has failed because it couldn't communicate with the NVIDIA driver."

    result = parse_query_output(line)

    assert result.gpus == []
    assert result.error == line


def test_no_devices_found_returns_error():
    result = parse_query_output("No devices were found")

    assert result.gpus == []
    assert result.error == "No devices were found"


def test_nan_value_is_none():
    result = parse_query_output("0, GPU, nan, 240, 405, 10.50, 0")

    assert result.error is None
    assert result.gpus[0].temperature_c is None


def test_parse_value_rejects_bad_values():
    assert parse_value(None) is None
    assert parse_value(123) is None
    assert parse_value("") is None
    assert parse_value("   ") is None
    assert parse_value("N/A") is None
    assert parse_value("[N/A]") is None
    assert parse_value("[Not Supported]") is None
    assert parse_value("[Unknown Error]") is None
    assert parse_value("nan") is None
    assert parse_value("inf") is None
    assert parse_value("-inf") is None
    assert parse_value("not a number") is None


def test_parse_value_accepts_finite_floats():
    assert parse_value("43") == 43.0
    assert parse_value(" 10.50 ") == 10.5
    assert parse_value("1e2") == 100.0


def test_index_non_digits_is_none_but_line_valid():
    result = parse_query_output("abc, GPU, 43, 240, 405, 10.50, 0")

    assert result.error is None
    assert result.gpus[0].index is None
    assert result.gpus[0].name == "GPU"


def test_empty_name_is_none():
    result = parse_query_output("0,,43,240,405,10.50,0")

    assert result.error is None
    assert result.gpus[0].name is None


def test_blank_lines_are_ignored():
    text = "\n0, GPU, 43, 240, 405, 10.50, 0\n\n"

    result = parse_query_output(text)

    assert result.error is None
    assert len(result.gpus) == 1

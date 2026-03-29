import subprocess

from tools.image_overlay_tool import ImageOverlayTool


def test_delayed_fade_overlay_shifts_pts_before_compositing(tmp_path, monkeypatch):
    video_path = tmp_path / "base.mp4"
    image_path = tmp_path / "overlay.webp"
    output_path = tmp_path / "out.mp4"
    video_path.write_bytes(b"")
    image_path.write_bytes(b"")

    captured = {}

    def _fake_run(cmd, capture_output, text, timeout):
        captured["cmd"] = cmd

        class _Result:
            returncode = 0
            stderr = ""

        return _Result()

    monkeypatch.setattr(subprocess, "run", _fake_run)

    tool = ImageOverlayTool()
    result = tool.add_images(
        video_path=str(video_path),
        output_path=str(output_path),
        images=[
            {
                "path": str(image_path),
                "start_time": 25.7,
                "end_time": 29.0,
                "animate_in": "fade",
                "animate_out": "fade",
                "animate_in_duration": 0.5,
                "animate_out_duration": 0.5,
            }
        ],
    )

    assert result.success is True
    cmd = captured["cmd"]
    filter_idx = cmd.index("-filter_complex")
    filter_graph = cmd[filter_idx + 1]

    assert "fade=t=in:st=0:d=0.5:alpha=1" in filter_graph
    assert "fade=t=out:st=2.8" in filter_graph
    assert ":d=0.5:alpha=1" in filter_graph
    assert "setpts=PTS-STARTPTS+25.700/TB" in filter_graph

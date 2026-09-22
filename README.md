# blog-code

The code and raw measurements behind posts on
[blog.kamelhar.net](https://blog.kamelhar.net).

An article that quotes a number should hand you the thing that produced it.
One directory per post.

| Directory | Post |
| --- | --- |
| [`dgx-spark-local-inference/`](dgx-spark-local-inference/) | [Building local inference on a DGX Spark](https://blog.kamelhar.net/serving-an-llm-on-an-nvidia-dgx-spark/) |

The configuration is what runs, one folder per step of the build, with private
addresses replaced by placeholders — replace those and it applies to your own
machine. The measurement tools take any OpenAI-compatible endpoint and need
nothing beyond the Python standard library.

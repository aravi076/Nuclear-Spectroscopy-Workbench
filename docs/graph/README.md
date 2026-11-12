# Code graph

[`nsw-code-graph.html`](nsw-code-graph.html) is an interactive map of the source code
and tests of this repository: every module, class and function is a node, edges are
calls, imports and references, and colours are clusters of closely connected code.
Open the file in a web browser (it works offline); use the search box to find a name
and click a node to see its connections.

Generated with [Graphify](https://pypi.org/project/graphifyy/) from the files of this
repository only (`graphify update .`, code extraction without a language model), on
2026-11-12. It is regenerated for releases; it may lag behind the code between them.

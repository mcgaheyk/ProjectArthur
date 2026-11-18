"""The idle show's scenes, one module each (D-0019).

Every module here whose name does not start with ``_`` is imported by
:func:`arthur.render.idle_show.load_plugins` and registers itself with
``@idle_show.scene(name)``. Replacing a scene means replacing its file; the engine does not
change. ``_common.py`` holds helpers the scenes share (the quiet part, walking, sitting).

A scene is ``(ctx: SceneContext) -> Sequence[Beat]``. Its beats must add up to at most
``ctx.frames`` and end with Arthur seated at rest at his spot on an otherwise empty stage.
"""

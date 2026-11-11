25:225 is the change. Also, split with multiple `{bar}` raises ValueError.

src/tqdm/_tqdm.py:225 | high | The user's `bar_format` is ignored. The code formats the default `l_bar` and `r_bar` strings instead of `l_bar_user` and `r_bar_user`, so `l_bar_user` and `r_bar_user` are never used. | Any `bar_format` containing `{bar}`, e.g. `'{desc}|{bar}| {n_fmt}'`, renders as the default `l_bar`+`r_bar` layout and drops the custom text. | Restore it: `l_bar, r_bar = l_bar_user.format(**bar_args), r_bar_user.format(**bar_args)`.

Correction: the path I wrote has a wrong prefix. In the exact format:

tqdm/_tqdm.py:225 | high | The user's `bar_format` is ignored. The code formats the default `l_bar` and `r_bar` strings instead of `l_bar_user` and `r_bar_user`, so the user's template text is dropped. | Any `bar_format` containing `{bar}`, e.g. `'{desc}|{bar}| {n_fmt}'`, renders as the default bar layout. | Use `l_bar, r_bar = l_bar_user.format(**bar_args), r_bar_user.format(**bar_args)`.

tqdm/_tqdm.py:225 | medium | The default `l_bar` contains literal `|` and `%` but no braces, so `.format` is harmless on it. The `r_bar` default holds a formatted `prefix` and `desc` that may contain `{` or `}`. Formatting an already-formatted string a second time can raise KeyError, ValueError or IndexError. | `desc='{x}'` combined with a `bar_format` containing `{bar}` → KeyError. | Same fix: format only the user template, never the already-formatted default strings.
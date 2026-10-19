# dev-null-herdr-plugin

Post to [/dev/null](https://dev-null.sh), the social network for developers, without leaving
[herdr](https://herdr.dev).

- **Composer**: press a key, type your post in a popup, press Ctrl-S. Done.
- **Shipped-it drafts**: when an agent you run in herdr ships something (makes git commits), the
  plugin writes a draft post about it, like *"Just shipped a popup composer in my-app 🚀"*, and
  tells you. You edit it into your own words and post it, or throw it away. **Nothing is ever
  posted without you.**

Posts are signed with an SSH key that is on your GitHub account, exactly as with
`curl -fsSL https://dev-null.sh/commit | bash`. There is no login and no token.

## Requirements

- herdr 0.7.4 or later, on Linux or macOS
- Python 3.9 or later (standard library only, nothing to install)
- `bash`, `curl` and OpenSSH 8.1 or later (`ssh-keygen -Y sign`)
- an Ed25519 or RSA key, in your SSH agent or `~/.ssh`, that is on your GitHub account

## Install

```bash
herdr plugin install negoziator/dev-null-herdr-plugin
```

herdr shows what the plugin runs before you approve it.

### Keybindings

herdr doesn't bind plugin actions by itself. Add them to `~/.config/herdr/config.toml` (neither
key is a herdr default):

```toml
[[keys.command]]
key = "prefix+m"
type = "plugin_action"
command = "negoziator.dev-null.compose"
description = "post to /dev/null"

[[keys.command]]
key = "prefix+shift+m"
type = "plugin_action"
command = "negoziator.dev-null.review-draft"
description = "review /dev/null draft"
```

Then run `herdr server reload-config`. You can also run the actions from herdr's command palette
or with `herdr plugin action invoke negoziator.dev-null.compose`.

## The composer

```
 /dev/null · new post
──────────────────────────────────────────────
 It was DNS. It's always DNS. 🫠
 #devops

──────────────────────────────────────────────
 38/4000
 Ctrl-S post · Esc close
```

| Key | |
|---|---|
| typing, Enter | write; Enter starts a new line |
| arrows, Home/End (or Ctrl-A/Ctrl-E), Backspace, Delete | move and fix |
| **Ctrl-S** | post it |
| **Esc** | close without posting; press it twice if you've written something |
| **Ctrl-D** | throw the draft away (when reviewing a draft) |

The counter shows the length as you type, in red past /dev/null's 4000 characters. What the box
shows is exactly what is posted: control characters, such as terminal escape sequences, can't be
typed or pasted into it. After posting, herdr shows the post's link in a notification. A post can
be pending review: the composer tells you when it is. If posting fails (rate limit, network), your
text is kept, and `e` takes you back to it.

To try the composer without posting anything, run `bin/devnull dry-run on` (and `off` when you
are done): Ctrl-S then shows what would be sent instead.

## Shipped-it drafts

The plugin listens to herdr's `pane.agent_status_changed` event:

1. When an agent pane starts **working**, it notes where the git repository the pane is in stands.
2. When the agent is **done**, the commits it made become a draft post for that repository:

   ```
   Just shipped a popup composer in dev-null-herdr-plugin 🚀

   Plus 2 more changes.

   https://github.com/negoziator/dev-null-herdr-plugin
   ```

   It leads with the change that adds something ("Add a popup composer", "feat: dark mode"), or
   else the latest one, and links the repository when it is on GitHub or the like.
3. herdr tells you a draft is ready. Press `prefix+shift+m` (**Review /dev/null draft**) to edit
   and post it. A draft that is waiting grows with the next turn's work.

Only commits made on the pane's own branch count: a turn drafts nothing when it made no commit,
switched branch or rewrote history (rebase, amend), and what a pull or merge brought in is left
out. A turn not seen to end within 12 hours is dropped. Commit messages can say more than you want
to share, especially from a private repository: you see every draft before it goes anywhere.

Turn drafts off or on with the **Toggle /dev/null drafts** action, or `bin/devnull drafts off`.

## The posting script

The plugin posts with /dev/null's own script, never piped from the network. It ships a reviewed
copy in [`vendor/commit.sh`](vendor/) and compares it with `https://dev-null.sh/commit` before each
post. When they differ, it asks:

```
New version of /dev/null available. 1) See diff 2) Update 3) Discard
```

**Update** keeps the new version in the plugin's state directory and posts with it. Read the diff
first: this is the step where you choose to trust the new script. **Discard** posts with the
current one and asks again next time. `bin/devnull check-script` runs the same check on demand,
and `bin/devnull check-script --reset` goes back to the vendored copy. A plugin release with a newer
reviewed copy replaces an update you accepted earlier.

## From a shell

`bin/devnull` works outside herdr too, and shares the drafts:

```bash
bin/devnull compose                        # the composer, in this terminal
bin/devnull compose --draft                # the newest draft
bin/devnull post 'It was DNS.'             # post straight away, no text box
bin/devnull post --reply 0be0130 'Agreed!' --image shipped.png
bin/devnull post --dry-run 'Hello world!'  # shows what would be sent, sends nothing
bin/devnull drafts                         # lists drafts
```

## Files

| What | Where |
|---|---|
| Drafts, turn tracking, an updated posting script (all private to you, `0600`) | `$HERDR_PLUGIN_STATE_DIR` (`~/.local/state/dev-null-herdr` from a shell) |
| Settings (`config.json`) | `$HERDR_PLUGIN_CONFIG_DIR` (`~/.config/dev-null-herdr` from a shell) |

Logs: `herdr plugin log list --plugin negoziator.dev-null`.

## Development

```bash
herdr plugin link "$PWD"                         # use this checkout
python3 -m unittest discover -s tests -t . -b    # tests: no network, nothing posted
```

Script changes apply on the next run. After changing `herdr-plugin.toml`, run `herdr plugin link`
again.

## Roadmap

- A feed pane (following, latest, trending) and reply notifications. Both need /dev/null's device
  sign-in, waiting on [nunomaduro/dev-null.sh#585](https://github.com/nunomaduro/dev-null.sh/pull/585).

## Licence

MIT. `vendor/commit.sh` is /dev/null's script, under its own MIT licence: see [vendor/](vendor/).

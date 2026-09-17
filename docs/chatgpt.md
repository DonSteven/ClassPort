# Using ClassPort with ChatGPT

[English](./chatgpt.md) | [简体中文](./chatgpt.zh-CN.md)

ClassPort keeps your courses, deadlines, study progress, and uploaded material
in one place. ChatGPT on the web can read and update that data through the
ClassPort MCP plugin. The dashboard remains available for direct editing and
file uploads.

## Connect in the browser

Deploy ClassPort behind public HTTPS and set `PUBLIC_URL` to that origin.
Follow [INSTALL §7](../INSTALL.md#7-connect-chatgpt) to add the `/mcp` endpoint
in [ChatGPT Plugins](https://chatgpt.com/plugins), choose OAuth, install it,
and select it with `@` in a new conversation. Account and workspace permissions
apply; a subscription alone does not guarantee custom-server access.

ClassPort advertises dynamic client registration, public-client token exchange
(`none`), and PKCE-S256. Leave static client credentials blank. It does not
advertise CIMD or refresh-token support. Complete the login and consent with
your own ClassPort account; reconnect when the access token expires. See the
[official authentication guide](https://developers.openai.com/plugins/build/auth)
for how ChatGPT discovers and authorizes MCP servers.

Start with: **“Use ClassPort to get my settings, list my courses, and show this
week's deadlines.”** For changes, review what ChatGPT proposes and confirm
when prompted. Check the saved result in the dashboard.

## Work with course files

| Goal | How to do it |
| --- | --- |
| Save PDFs or notes for future conversations | Upload them in ClassPort's **Files** view, under a course folder such as `STAT201/`. |
| Find saved material | Ask ChatGPT to call `list_course_files` with the folder prefix; listings are one level deep. |
| Read a note or selected PDF pages | Use `read_course_file` with the returned path and, for PDFs, `pages="12"` or `pages="3-8"` (at most 20 pages per call). |
| Explain a chart or scanned page | Attach the original PDF or a page image to the ChatGPT conversation if the tool-returned image is unavailable. |
| Revise `course.md` | Ask ChatGPT for revised Markdown, review it, then upload the replacement in ClassPort. |

The file tools expose **listing and reading**. They do not provide a chat file
picker, a local-folder watcher, or a file upload/edit tool. Chat attachments are
not automatically persisted in ClassPort. Uploading a file to ClassPort also
does not grant ChatGPT access to your university's LMS.

PDF reads return extracted text with page numbers alongside rendered images.
Text extraction is not OCR: a scan or diagram can have no usable text. ChatGPT's
handling of MCP images can differ from its handling of direct chat attachments;
do not infer visual details from a successful tool call alone. Community
[reports and an OpenAI support reply](https://community.openai.com/t/chatgpt-connector-returns-empty-for-mcp-tool-results-with-type-image-while-same-tool-works-in-mcp-inspector/1375446)
describe this limitation. Treat them as reports about those surfaces, not a
guarantee that every account behaves identically.

## Study and plan

Paste the [study prompt](./chatgpt-study-prompt.md) into a connected conversation.
Examples:

- “Read STAT201/course.md and propose courses, weekly slots, exam details, and deadlines. Show me the plan before saving.”
- “Record today's STAT201 lecture on Gamma distributions, normal approximations, and binomial proportion estimation, then mark attendance.”
- “Read problem 4 in STAT201/homework-05.pdf. Explain the Gamma distribution and its normal approximation, then ask a check question.”
- “Mark binomial proportion estimation studied and show my remaining topics.”

Instructions do not install a plugin or grant access. Select ClassPort in the
conversation where you want to use it. This workflow uses your ChatGPT account
and the self-hosted server, without adding an OpenAI API key to ClassPort.

## Troubleshoot the connection

- **No custom-server option:** check account access and workspace permissions
  against the [official setup guide](https://developers.openai.com/api/docs/guides/custom-mcp-server).
- **Discovery or login fails:** verify public HTTPS, `PUBLIC_URL`, and reachability
  of `/.well-known/oauth-authorization-server`,
  `/.well-known/oauth-protected-resource/mcp`, and `/oauth/register`. Preserve
  the OAuth callback URI supplied by ChatGPT; do not replace it with a local URL.
- **401 after a previously working connection:** reconnect ClassPort from the
  plugin's connection controls and sign in again. This server has no refresh
  tokens. A [community OAuth report](https://community.openai.com/t/chatgpt-does-not-re-trigger-oauth-on-401-www-authenticate-for-mcp-tool-calls/1374168)
  also shows why a bare 401 should not be assumed to open a reauthorization UI.
- **Tools are missing after an update:** refresh the plugin's tools and start
  a new conversation; see [OpenAI's connection guide](https://developers.openai.com/api/docs/guides/custom-mcp-server).
- **A PDF returns text but the chart is missing:** attach the page directly to
  the conversation. Do not ask the model to invent the missing diagram.

This is an interactive custom MCP plugin. The `search`/`fetch` compatibility
interface used by company knowledge and deep research is a separate integration;
ClassPort does not claim to implement it. See [OpenAI's MCP guide](https://developers.openai.com/api/docs/mcp).

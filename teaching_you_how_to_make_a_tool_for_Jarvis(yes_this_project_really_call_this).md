# Preface
It's very easy to build a tool for J.A.R.V.I.S. Here are some Mandatory Rules.

# Role
- First, this project is built in Python, so your tool must be a Python file.

- Second, you need to expose a run function to give J.A.R.V.I.S. an input area when it uses your tool.
    -The run function also has the following requirements:
    + It must accept an  parameter, which should be a dict. You need to parse it inside your function. (Sorry, I know it's troublesome to do it this way.oh!You need to handle errors.look at this[error](./teaching_you_how_to_make_a_tool_of_Jarvis%28yes_this_project_really_call_this%29.md#L15-L16))
    + Suggestion: you can name this parameter params (because it sounds good), but you don't have to.

- Third, it must return a str!
You might not comply, but your baby J.A.R.V.I.S. probably won't be able to see what your tool does. So your tool must return a value.
Wait,and...look:
if YourToolError: return "Status:Error,reason:<your reason>" (or any format you like — as long as it's useful and parsable, we don't care.)
Understand?i know you are the smarest.
- Fourth, your tool's root directory must contain a file named .l.json (e.g., you can name it clock.l.json). The system only checks whether there is a .l.json file in that directory.
Here is an example of what to write in this file:

```json
{
    "name": "write",
    "ToolPath": "./write.py",
    "is_builtin": false,
    "description": "覆盖写入文件。直接将内容覆盖写入指定路径。",
    "use_case": "创建新文件、覆盖修改文件内容时使用。",
    "keywords": ["写入", "文件", "创建", "保存", "覆盖", "write"],
    "params_example": "{\"name\": \"write\", \"parameters\": {\"path\": \"完整路径\", \"content\": \"文件内容\"}}",
    "note": "会覆盖原文件。父目录不存在时自动创建。生成文件内容必须用此工具落盘，不要粘贴在回复中。",
    "Accessible": ["TaskAgent","subAgent"]
}
```
> tips for "ToolPath":and you can write "write.py" too.It depends on your file location.
- Fifth, your tool must be packaged as an Archive (zip, 7z, rar, tar, gz, etc. are all fine).you can do not,but.....
it's hard to install your tool,user must replace the file oneself

- sixth,The tool path is the `.py` file containing the `run` function, relative to the `.l.json` file. That is: if `a.py` is in the same directory as `.l.json`, write `./a.py`; if it is in `./b/a.py`, write `./b/a.py`.

Your stuff, your rules. As long as it doesn't harm user interests, we don't enforce variable parameter naming and error handling....return what you want to return, but don't write be like "i don't know!~" — your error message should actually help someone fix the problem.

At last,whis you good luck.(if you have courage,come here[challenge](./Challenge_of_every%28even_if_you_are_an_ai%29.md))
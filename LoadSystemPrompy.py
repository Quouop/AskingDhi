def replace_placeholders(file_path, user_name, user_identity, project_path,tool_list):
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except FileNotFoundError:
        raise FileNotFoundError(f"文件不存在: {file_path}")
    except IOError as e:
        raise IOError(f"读取文件失败: {e}")

    content = content.replace('{USER_NAME}', user_name)
    content = content.replace('{USER_IDENTITY}', user_identity)
    content = content.replace('{PROJECT_PATH}', project_path)
    content = content.replace('{TOOL_LIST}',tool_list)
    return content
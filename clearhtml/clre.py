import re
import json
import os

class ContentCleaner:
    def __init__(self, config_path=None):
        # 如果未指定路径，则使用脚本所在目录下的 BlockedWord.json
        if config_path is None:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            config_path = os.path.join(script_dir, 'BlockedWord.json')
        self.config_path = config_path
        
        # 加载关键词并编译正则（如果文件不存在或解析失败，会用内置词库）
        self.noise_regex = re.compile(
            self._get_footer_regex_str(),
            re.IGNORECASE
        )
        self.start_markers = ["正在阅读：", "本文来源：", "核心提示："]
        self.tail_lines_count = 20

    def _get_footer_regex_str(self):
        """从 JSON 配置中读取所有关键词，拼接成忽略大小写的正则"""
        if not os.path.exists(self.config_path):
            print(f"警告：配置文件 {self.config_path} 不存在，使用内置默认词库。")
            return self._default_regex()
        
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                data = json.load(f)  # 直接加载为字典
        except Exception as e:
            print(f"读取配置文件失败：{e}，使用内置默认词库。")
            return self._default_regex()
        
        all_keywords = []
        for cat_list in data.get("categories", {}).values():
            all_keywords.extend(cat_list)
        
        # 去重并过滤空字符串
        all_keywords = list(set([kw for kw in all_keywords if kw]))
        if not all_keywords:
            return "(?!)"  # 永不匹配
        
        escaped = [re.escape(kw) for kw in all_keywords]
        combined = "(?i)(" + "|".join(escaped) + ")"
        return combined

    def _default_regex(self):
        """内置默认词库（已包含“点击收起全文”）"""
        defaults = [
            "京ICP", "粤ICP", "沪ICP", "浙B2", "公安备案", "公网安备",
            "经营许可证", "网络文化经营", "出版物经营", "统一社会信用代码",
            "Copyright ©", "All Rights Reserved", "版权所有", "翻录必究",
            "法律支持", "扫一扫", "扫码", "二维码", "长按识别", "打开微信",
            "打开手机", "关注公众号", "下载APP", "小程序", "手机摄像头",
            "企业微信", "抖音扫一扫", "评论", "留言", "发言", "投诉",
            "举报", "意见反馈", "违法和不良信息", "评价", "跟帖", "我要评价",
            "已显示所有评论", "关闭", "跳过", "不再提示", "残忍拒绝",
            "我知道了", "返回顶部", "跳过广告", "×", "暂不参与", "确定关闭",
            "关闭浮窗", "点击此处关闭弹窗", "关于我们", "联系我们",
            "客服电话", "帮助中心", "常见问题", "隐私政策", "用户协议",
            "免责声明", "网站地图", "技术支持", "公司地址", "工作时间",
            "商务合作", "本站支持IPv6访问", "建议使用Chrome", "浏览器升级",
            "仅对APP用户开放", "请扫码下载", "致电客服", "在线留言",
            "广告位招租", "浏览器版本过低", "关闭兼容模式", "提供技术支持",
            "加载更多", "没有更多数据", "我是有底线的", "点击收起全文"
        ]
        escaped = [re.escape(kw) for kw in defaults]
        combined = "(?i)(" + "|".join(escaped) + ")"
        return combined

    def clean(self, raw_text: str) -> str:
        if not raw_text:
            return ""

        lines = raw_text.split('\n')

        # 处理顶部：用 start_markers 截掉导航栏
        text = raw_text
        for marker in self.start_markers:
            pos = text.find(marker)
            if pos != -1:
                text = text[pos:]
                break
        lines = text.split('\n')

        # 如果内容太短，直接返回
        if len(lines) <= self.tail_lines_count:
            return text

        body_lines = lines[:-self.tail_lines_count]
        tail_lines = lines[-self.tail_lines_count:]

        # 过滤尾部噪音行
        filtered_tail = [
            line for line in tail_lines
            if not self.noise_regex.search(line.strip())
        ]

        clean_text = '\n'.join(body_lines + filtered_tail)
        return clean_text.strip()


# ---------- 测试 ----------
if __name__ == "__main__":
    # 如果 BlockedWord.json 存在则加载，否则使用内置词库
    cleaner = ContentCleaner()

    test_text = """# 2026年中国AI发展趋势前瞻

来源：[新华网](http://www.xinhuanet.com/20260128/037b1159b26645dea4648c535571ca3e/c.html ) | 2026年01月28日 11:51:09

...（中间省略正文）...

编辑：王玉西 责任编辑：刘亮



点击收起全文

[返回央视网首页](http://www.cctv.com/) [返回新闻频道](http://news.cctv.com/)

分享：

扫一扫 分享到微信

|

[望海热线](https://www.cctv.com/special/guanyunew/jianjiePAGEQezAEWqHSGGmjjQL6s0V231214/index.shtml)

[加载更多](javascript:;)

[首页](http://m.cctv.com/)|[全站地图](http://m.cctv.com/quanzhannav2019/index.shtml)

[京ICP备10003349号-1](https://beian.miit.gov.cn/)中央广播电视总台央视网版权所有

![](//p1.img.cctvpic.com/photoAlbum/templet/common/DEPA1565315968922641/yangshiwang_logo_18897_190809.png) **正在阅读：**2026年中国AI发展趋势前瞻

扫一扫 分享到微信

手机看

扫一扫 手机继续看

A- A+

 ..."""

    cleaned = cleaner.clean(test_text)
    # 打印清洗后的末尾10行，确认尾部噪音已被去除
    lines_after = cleaned.split('\n')
    print("===== 清洗后末尾10行 =====")
    print("\n".join(lines_after[-10:]))
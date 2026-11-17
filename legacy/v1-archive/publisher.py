"""平台发布适配接口（空实现）。

当前**不接自动发布**：B 站/抖音的开放平台需要企业资质、应用审核与账号 OAuth 授权，
本版本只登记内容计划、回填已发布数据，不做任何自动投递。
接入时实现 Publisher.publish：上传素材、调用平台投稿接口、回写 published_at 与视频 ID。
"""


class Publisher:
    """占位适配器：明确拒绝自动发布，避免把"能登记"误当成"能发"。"""

    def publish(self, plan):
        raise NotImplementedError(
            "未接自动发布：需要平台开放接口授权（企业资质/应用审核/账号 OAuth），"
            "本版本只做计划与数据回填")


def get_publisher():
    return Publisher()

  export const ROLE_META = {
    auditor:        { name:'审计',     color:0x6B4FA3, hair:0x1c1c24, skin:0xD9A67E, female:0 },
    'chief-of-staff':{name:'幕僚长',   color:0x4C5A6E, hair:0x555a60, skin:0xD9A67E, female:0 },
    coach:          { name:'教练',     color:0x5F9E4A, hair:0x4a3320, skin:0xE6B896, female:0 },
    content:        { name:'内容',     color:0xC4566F, hair:0x1c1c1c, skin:0xF0C8A0, female:1 },
    finance:        { name:'财务',     color:0x3A7CA5, hair:0x2b1d12, skin:0xD9A67E, female:0 },
    hr:             { name:'HR',       color:0x7E5AA8, hair:0x1c1c24, skin:0xE6B896, female:1 },
    planner:        { name:'规划师',   color:0x2F9E8F, hair:0x2b1d12, skin:0xE6B896, female:1 },
    pm:             { name:'产品经理', color:0x3F6FB5, hair:0x3a2a1c, skin:0xF0C8A0, female:1 },
    producer:       { name:'制作人',   color:0xD08A2E, hair:0x6a3b1a, skin:0xF0C8A0, female:0 },
    scout:          { name:'商机侦察', color:0x8A6A45, hair:0x2a2a2a, skin:0xD9A67E, female:0 },
    secretary:      { name:'秘书',     color:0xC97B2E, hair:0x1c1c1c, skin:0xE6B896, female:1 }
  };

  // 性格/台词/动画参数/交付偏好 —— 每个 agent 一份，唯一真源（避免多处采样造成风格漂移）
  export const PROFILE = {
    auditor: {
      mood: '严谨 · 慢热', pace: 0.85, typingMul: 1.1, loiterChance: 0.003, chatChance: 0.1,
      rushChance: 0.25, delivery: 'board',
      speeches: {
        idle: ['账对不上啊…','证据呢？','再核一遍','数字得对','先不动'],
        working: ['查一下凭证','复核中…','别信直觉，看数'],
        loiter: ['我看看大家','顺便核一下看板','走动走动更清楚'],
        rush: ['还差一个数！','快！有遗漏！'],
        deliver: ['审完了，看板已更新','证据齐了','签个字吧']
      }
    },
    'chief-of-staff': {
      mood: '稳 · 话少', pace: 0.95, typingMul: 0.9, loiterChance: 0.002, chatChance: 0.15,
      rushChance: 0.15, delivery: 'longyi',
      speeches: {
        idle: ['嗯','等下','整理中'],
        working: ['按优先级排','先别吵，我统筹'],
        loiter: ['看看各部','摸个底'],
        rush: ['这事得马上过老板'],
        deliver: ['老板要的汇总','各方意见收齐了','请龙总过目']
      }
    },
    coach: {
      mood: '阳光 · 爱唠叨', pace: 1.1, typingMul: 0.85, loiterChance: 0.006, chatChance: 0.5,
      rushChance: 0.1, delivery: 'random',
      speeches: {
        idle: ['今天状态不错','有人需要辅导吗','站起来活动活动'],
        working: ['我在做辅导方案','先问清楚目标'],
        loiter: ['哎，那边那个谁','来跟我聊聊','大家最近咋样'],
        rush: ['这个得赶紧鼓励一下！'],
        deliver: ['你可以的！看看这个方案','给你一套方法']
      }
    },
    content: {
      mood: '敏感 · 多产', pace: 1.0, typingMul: 1.3, loiterChance: 0.004, chatChance: 0.25,
      rushChance: 0.3, delivery: 'board',
      speeches: {
        idle: ['写什么好呢…','灵感呢…','喝口咖啡先'],
        working: ['这个开头不对','改第 17 版了','先出草稿'],
        loiter: ['去看看市场上怎么写','找找感觉'],
        rush: ['截稿了！截稿了！'],
        deliver: ['草稿在看板','标题又改了三次','先凑合看']
      }
    },
    finance: {
      mood: '抠门 · 精确', pace: 0.9, typingMul: 1.05, loiterChance: 0.002, chatChance: 0.08,
      rushChance: 0.2, delivery: 'longyi',
      speeches: {
        idle: ['再算一遍','差一分钱'],
        working: ['对账中','等我把银行流水贴进去'],
        loiter: ['去看看合同档案','顺便贴个发票'],
        rush: ['报税逾期罚款了！'],
        deliver: ['账平了','发票归档好了','请龙总签这张']
      }
    },
    hr: {
      mood: '亲和 · 八卦', pace: 1.05, typingMul: 0.95, loiterChance: 0.005, chatChance: 0.6,
      rushChance: 0.1, delivery: 'random',
      speeches: {
        idle: ['今天没人请假吧','谁过生日','茶歇要不要换'],
        working: ['JD 还得再润色','先面这三个'],
        loiter: ['哎，你们部门最近咋样','吃了吗','顺便传个八卦'],
        rush: ['候选人到楼下了！'],
        deliver: ['候选人简历','新员工入职流程','本月 HR 月报']
      }
    },
    planner: {
      mood: '理智 · 抽象', pace: 0.95, typingMul: 1.0, loiterChance: 0.003, chatChance: 0.2,
      rushChance: 0.15, delivery: 'board',
      speeches: {
        idle: ['这路对吗','先想清楚再动手','画个图先'],
        working: ['WBS 第 4 层展开','路径分析中'],
        loiter: ['去现场看看','核对依赖'],
        rush: ['里程碑 3 天！重排！'],
        deliver: ['计划更新了','关键路径调整','看板已同步']
      }
    },
    pm: {
      mood: '焦虑 · 懂业务', pace: 1.15, typingMul: 1.15, loiterChance: 0.004, chatChance: 0.35,
      rushChance: 0.5, delivery: 'random',
      speeches: {
        idle: ['用户到底要啥…','这个需求对吗','先写 PRD'],
        working: ['拆解到用户故事','和设计对齐中'],
        loiter: ['去和内容聊聊','找找开发','确认需求没跑偏'],
        rush: ['演示会还有 2 小时！'],
        deliver: ['PRD 第 8 版','原型改了','看板已拆到用户故事']
      }
    },
    producer: {
      mood: '强势 · 结果导向', pace: 1.2, typingMul: 1.2, loiterChance: 0.002, chatChance: 0.1,
      rushChance: 0.4, delivery: 'longyi',
      speeches: {
        idle: ['资源先算清楚','风险在哪','等会儿碰一下'],
        working: ['赶这期上线','排期卡死'],
        loiter: ['盯一下 B 排的进度','看内容那边出了没'],
        rush: ['上线前发现 bug！','给我排人！'],
        deliver: ['这期上线','成本省了 12%','请龙总确认资源']
      }
    },
    scout: {
      mood: '外向 · 爱跑', pace: 1.3, typingMul: 0.7, loiterChance: 0.012, chatChance: 0.4,
      rushChance: 0.5, delivery: 'random',
      speeches: {
        idle: ['今天去哪扫街','刷下行业','电话打起来'],
        working: ['在聊一个客户','线索跟进中'],
        loiter: ['我出去转一圈','外面有个活动','看看竞争对手'],
        rush: ['客户刚说要签！','预算有了！'],
        deliver: ['新线索 3 条','这家有意思','请老板过目']
      }
    },
    secretary: {
      mood: '勤快 · 周到', pace: 1.05, typingMul: 1.1, loiterChance: 0.003, chatChance: 0.2,
      rushChance: 0.3, delivery: 'longyi',
      speeches: {
        idle: ['会议安排上了','快递要签收','今天日程'],
        working: ['纪要整理中','邮件回一下'],
        loiter: ['去拿个文件','顺便给大家倒杯水'],
        rush: ['老板会议 5 分钟后开始！'],
        deliver: ['会议纪要','日程排好了','请龙总签收']
      }
    }
  };
  // 通用回退台词（PROFILE 里没覆盖的 agent 用）
  export const COMMON_SPEECH = {
    idle: ['歇会儿…','想想…','等一等'],
    working: ['执行中…','别吵','马上好'],
    loiter: ['走动一下','看看大家'],
    rush: ['快！','出事了！'],
    deliver: ['搞定','给你','看板更新']
  };
  export function pickSpeech(role, slot) {
    const pool = (PROFILE[role] && PROFILE[role].speeches && PROFILE[role].speeches[slot]) || COMMON_SPEECH[slot];
    if (!pool || !pool.length) return '';
    return pool[Math.floor(Math.random() * pool.length)];
  }
  export const ROLE_ORDER = Object.keys(ROLE_META);
  export const OWNER_LABEL = { longyi:'龍奕', ceo:'CEO' };
  ROLE_ORDER.forEach(k => { OWNER_LABEL[k] = ROLE_META[k].name; });

/**
 * 知乎问题页面回答容器调试脚本
 * 在知乎问题页面（如 https://www.zhihu.com/question/123456）打开浏览器控制台（F12），粘贴运行
 */

(function() {
    console.log('=== 知乎回答容器调试 ===\n');

    // 1. 查找所有可能包含回答的容器
    console.log('--- 尝试多种选择器 ---\n');

    const selectors = [
        // 知乎常用的回答容器
        '.List-item',
        '.AnswerItem',
        '.QuestionAnswer-content',
        '[class*="List-item"]',
        '[class*="AnswerItem"]',
        '[class*="QuestionAnswer"]',
        '.question-item',
        '[data-za-module*="AnswerItem"]',
        'div[itemtype*="Answer"]',
        '.AnswerTab-content',
        // 新版知乎
        '.List-item[data-za-index]',
        '[class*="AnswerList"]',
        '[class*="answer-list"]'
    ];

    let totalFound = 0;
    selectors.forEach(sel => {
        try {
            const elements = document.querySelectorAll(sel);
            if (elements.length > 0) {
                console.log(`✅ ${sel}: 找到 ${elements.length} 个元素`);

                // 输出第一个元素的详细信息
                if (elements.length > 0) {
                    const first = elements[0];
                    console.log(`   第一个元素的 class: ${first.className}`);
                    console.log(`   第一个元素的 id: ${first.id || '(无)'}`);
                    console.log(`   第一个元素的 data属性:`, first.dataset);

                    // 检查内部结构
                    const innerContent = first.querySelector('[class*="RichText"], [class*="ztext"], [itemprop="text"]');
                    if (innerContent) {
                        console.log(`   包含内容元素: ${innerContent.className}`);
                    }
                }
                totalFound += elements.length;
            }
        } catch(e) {}
    });

    console.log(`\n总共找到约 ${totalFound} 个元素\n`);

    // 2. 尝试精确定位回答内容区域
    console.log('--- 精确定位回答内容 ---\n');

    // 查找包含 RichText 或 ztext 的元素
    const contentSelectors = [
        '[class*="RichText"]',
        '[class*="ztext"]',
        '[itemprop="text"]',
        '[class*="answer-content"]',
        '[class*="content-body"]'
    ];

    contentSelectors.forEach(sel => {
        const elements = document.querySelectorAll(sel);
        if (elements.length > 0) {
            console.log(`📝 内容元素 ${sel}: ${elements.length} 个`);
        }
    });

    // 3. 找到回答列表的父容器
    console.log('\n--- 查找回答列表父容器 ---\n');

    // 向上查找包含多个回答的容器
    const answerListContainers = document.querySelectorAll(
        '[class*="QuestionAnswers"], ' +
        '[class*="AnswerList"], ' +
        '[class*="List"], ' +
        '.Question-main'
    );

    answerListContainers.forEach(container => {
        const directChildren = container.children;
        console.log(`📦 容器 ${container.className}: 有 ${directChildren.length} 个直接子元素`);
    });

    // 4. 输出最终建议
    console.log('\n=== 调试完成 ===');
    console.log('请将上述结果复制给 AI，以便更新爬虫选择器\n');

    // 返回建议的选择器
    return {
        recommendedSelectors: {
            answerContainer: '.List-item, [class*="AnswerItem"], [class*="List-item"]',
            answerContent: '[class*="RichText"], [class*="ztext"]',
            authorInfo: '.AuthorInfo-name, [class*="author-name"]'
        }
    };
})();

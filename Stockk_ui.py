import streamlit as st
import yfinance as yf
import vertexai
from vertexai.generative_models import GenerativeModel
import logging
import traceback
from typing import Dict, Optional, List, Tuple
import pandas as pd
import requests
from bs4 import BeautifulSoup
import time
import feedparser
from datetime import datetime
import re
import random
import os

# 設置日誌
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Vertex AI 設定
PROJECT_ID = os.getenv("PROJECT_ID", "ageless-courier-460814-p0")
LOCATION = os.getenv("LOCATION", "us-central1")
MODEL_ID = os.getenv("MODEL_ID", "gemini-2.5-flash-preview-05-20")

class PTTScraper:
    def __init__(self, max_retries: int = 3, retry_delay: int = 5):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        })
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.cookies = {'over18': '1'}

    def _make_request(self, url: str) -> Optional[requests.Response]:
        """發送請求並處理重試邏輯"""
        for attempt in range(self.max_retries):
            try:
                response = self.session.get(url, cookies=self.cookies, timeout=10)
                response.raise_for_status()
                return response
            except requests.exceptions.RequestException as e:
                logger.warning(f"請求失敗 (嘗試 {attempt + 1}/{self.max_retries}): {e}")
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay + random.uniform(1, 3))
                continue
        return None

    def get_ptt_stock_posts(self, url: str = "https://www.ptt.cc/bbs/Stock/index.html", 
                           num_posts: int = 5) -> List[Dict]:
        """獲取 PTT Stock 版文章列表"""
        try:
            response = self._make_request(url)
            if not response:
                return []

            soup = BeautifulSoup(response.text, 'html.parser')
            posts_data = []

            for post_div in soup.find_all('div', class_='r-ent')[:num_posts]:
                try:
                    post_info = self._extract_post_info(post_div)
                    if post_info:
                        # 獲取文章內容
                        content = self._get_post_content(post_info['link'])
                        post_info['content'] = content
                        posts_data.append(post_info)
                except Exception as e:
                    logger.error(f"處理文章時發生錯誤: {e}")
                    continue

            return posts_data
        except Exception as e:
            logger.error(f"爬取 PTT 時發生錯誤: {e}")
            return []

    def _extract_post_info(self, post_div) -> Optional[Dict]:
        """從文章 div 中提取資訊"""
        try:
            title_tag = post_div.find('div', class_='title')
            if not title_tag or not title_tag.a:
                return None

            title = title_tag.a.text.strip()
            link = "https://www.ptt.cc" + title_tag.a['href']
            
            author_tag = post_div.find('div', class_='author')
            author = author_tag.text.strip() if author_tag else "N/A"
            
            date_tag = post_div.find('div', class_='date')
            date = date_tag.text.strip() if date_tag else "N/A"

            # 清理和驗證資料
            title = self._clean_text(title)
            author = self._clean_text(author)
            date = self._clean_text(date)

            return {
                'title': title,
                'link': link,
                'author': author,
                'date': date
            }
        except Exception as e:
            logger.error(f"提取文章資訊時發生錯誤: {e}")
            return None

    def _get_post_content(self, url: str) -> str:
        """獲取文章內容"""
        try:
            response = self._make_request(url)
            if not response:
                return "無法獲取文章內容"

            soup = BeautifulSoup(response.text, 'html.parser')
            content_div = soup.find(id='main-content')
            
            if not content_div:
                return "無法找到文章內容"

            # 移除不需要的元素
            for element in content_div.find_all(['div', 'span']):
                element.decompose()

            content = content_div.text.strip()
            return self._clean_text(content)
        except Exception as e:
            logger.error(f"獲取文章內容時發生錯誤: {e}")
            return "獲取文章內容時發生錯誤"

    @staticmethod
    def _clean_text(text: str) -> str:
        """清理文字內容"""
        # 移除多餘的空白
        text = re.sub(r'\s+', ' ', text)
        # 移除特殊字符
        text = re.sub(r'[^\w\s\u4e00-\u9fff.,!?，。！？]', '', text)
        return text.strip()

def get_rss_news(num_items: int = 3) -> List[Dict]:
    """從 Yahoo 奇摩股市 RSS 獲取新聞"""
    try:
        # Yahoo 奇摩股市的台股動態 RSS
        url = "https://tw.stock.yahoo.com/rss"
        feed = feedparser.parse(url)
        
        news_items = []
        for entry in feed.entries[:num_items]:
            news_items.append({
                'title': entry.title,
                'summary': entry.summary if hasattr(entry, 'summary') else '',
                'link': entry.link,
                'published': entry.published if hasattr(entry, 'published') else ''
            })
        return news_items
    except Exception as e:
        logger.error(f"獲取 RSS 新聞時發生錯誤: {e}")
        return []

def fetch_twse_stock_list() -> Dict[str, str]:
    """從台灣證券交易所網站獲取上市公司清單"""
    try:
        url = "https://isin.twse.com.tw/isin/class_main.jsp?owncode=&stockname=&isincode=&market=1&issuetype=1&industry_code=&Page=1&chklike=Y"
        response = requests.get(url)
        response.encoding = 'ms950'
        soup = BeautifulSoup(response.text, 'html.parser')
        
        stock_dict = {}
        for row in soup.find_all('tr')[1:]:  # 跳過標題行
            cols = row.find_all('td')
            if len(cols) >= 4:
                code = cols[2].text.strip()
                name = cols[3].text.strip()
                if code and name and code.isdigit():
                    stock_dict[name] = code
        
        return stock_dict
    except Exception as e:
        logger.error(f"獲取上市公司清單時發生錯誤: {e}")
        return {}

# 初始化 Vertex AI
try:
    print(f"\n[1] 正在初始化 Vertex AI...")
    vertexai.init(project=PROJECT_ID, location=LOCATION)
    print(f"[SUCCESS] Vertex AI 初始化成功。")

    print(f"\n[2] 正在載入模型: {MODEL_ID}...")
    model = GenerativeModel(MODEL_ID)
    print(f"[SUCCESS] 模型載入成功。")
except Exception as e:
    st.error(f"Vertex AI 初始化失敗: {e}")
    st.stop()

class StockAnalyzer:
    def __init__(self):
        self.model = model
        self.tw_stocks = fetch_twse_stock_list()
        self.ptt_scraper = PTTScraper()
        logger.info(f"已載入 {len(self.tw_stocks)} 家上市公司資料")

    def search_stocks(self, query: str) -> List[Tuple[str, str]]:
        """搜尋股票"""
        if not query:
            return []
        
        # 搜尋公司名稱或代號
        results = []
        for name, code in self.tw_stocks.items():
            if query.lower() in name.lower() or query in code:
                results.append((name, code))
        return results

    def get_stock_info(self, ticker: str) -> Dict:
        """獲取股票資訊"""
        try:
            # 確保股票代號格式正確
            if not ticker.endswith('.TW') and not ticker.endswith('.TWO'):
                ticker = f"{ticker}.TW"
            
            stock = yf.Ticker(ticker)
            info = stock.info
            
            # 檢查是否成功獲取資訊
            if not info:
                return {"error": f"無法獲取股票 {ticker} 的資訊"}
            
            # 獲取歷史資料
            hist = stock.history(period="5d")
            
            # 格式化數值
            if 'marketCap' in info and info['marketCap']:
                info['marketCap'] = f"{info['marketCap']/100000000:.2f}億"
            
            return {
                "info": info,
                "history": hist if not hist.empty else None
            }
        except Exception as e:
            logger.error(f"查詢股票 {ticker} 時發生錯誤: {e}")
            traceback.print_exc()
            return {"error": f"查詢股票 {ticker} 時發生錯誤: {e}"}

    def ask_gemini_for_term(self, term: str) -> Dict:
        """使用 Gemini 解釋金融名詞"""
        prompt = f"""角色：你是一位精通金融的AI助理。
任務：請用繁體中文向一位金融新手解釋「{term}」是什麼。
請盡量使用生活化的比喻，並說明其重要性。避免提供任何投資建議。

請嚴格按照以下格式輸出（不要添加任何其他內容）：
名詞解釋：[此處填寫名詞解釋]
重要性：[此處填寫重要性說明]
生活比喻：[此處填寫生活化比喻]"""
        try:
            response = self.model.generate_content(prompt)
            
            # 解析 Gemini 的回應
            lines = response.text.split('\n')
            analysis_result = {}
            for line in lines:
                if line.startswith("名詞解釋："):
                    analysis_result['definition'] = line.replace("名詞解釋：", "").strip()
                elif line.startswith("重要性："):
                    analysis_result['importance'] = line.replace("重要性：", "").strip()
                elif line.startswith("生活比喻："):
                    analysis_result['analogy'] = line.replace("生活比喻：", "").strip()
            return analysis_result
        except Exception as e:
            logger.error(f"Gemini 解釋「{term}」時發生錯誤: {e}")
            traceback.print_exc()
            return {"error": f"Gemini 解釋「{term}」時發生錯誤: {e}"}

    def analyze_news_with_gemini(self, title: str, summary: str) -> Dict:
        """使用 Gemini 分析新聞"""
        prompt = f"""角色：你是一位資深財經記者，也是一位擅長向新手解釋股市的導師。
任務：請閱讀以下財經新聞標題與摘要，並完成下列任務：
1. 判斷此新聞對相關股票的潛在影響是偏「利多」、「利空」，還是「中性」。
2. 針對這則新聞，撰寫一段約50-100字的「新手看法解釋」，說明這則新聞為什麼被認為是利多/利空/中性，以及它通常可能對股價產生什麼樣的初步影響。請避免使用過於專業的術語，並強調這不是投資建議。

新聞標題：{title}
新聞摘要：{summary}

請嚴格按照以下格式輸出（不要添加任何其他內容）：
情感判斷：[利多/利空/中性]
新手看法：[此處填寫新手看法解釋]"""
        try:
            response = self.model.generate_content(prompt)
            
            # 解析 Gemini 的回應
            lines = response.text.split('\n')
            analysis_result = {}
            for line in lines:
                if line.startswith("情感判斷："):
                    analysis_result['sentiment'] = line.replace("情感判斷：", "").strip()
                elif line.startswith("新手看法："):
                    analysis_result['novice_explanation'] = line.replace("新手看法：", "").strip()
            return analysis_result
        except Exception as e:
            logger.error(f"Gemini 分析新聞時發生錯誤: {e}")
            traceback.print_exc()
            return {"error": f"Gemini 分析新聞時發生錯誤: {e}"}

    def summarize_ptt_post(self, content: str, num_sentences: int = 3) -> str:
        """使用 Gemini 進行 PTT 文章摘要"""
        prompt = f"""
        角色：你是一位專業的內容編輯。
        任務：請將以下提供的文本內容，用繁體中文摘要成 {num_sentences} 句話的重點。

        文本內容：
        ---
        {content}
        ---
        摘要：
        """
        try:
            response = self.model.generate_content(prompt)
            return response.text
        except Exception as e:
            logger.error(f"Gemini 摘要時發生錯誤: {e}")
            traceback.print_exc()
            return f"摘要生成失敗: {str(e)}"

# --- Streamlit UI ---
st.set_page_config(page_title="小股神助理 (Gemini版)", layout="wide")
st.title("小股神助理 (Gemini API 實戰版)")

# 初始化分析器
analyzer = StockAnalyzer()

# 功能1: 解釋金融名詞
st.header("📚 金融名詞解釋")
term_input = st.text_input("輸入你想查詢的金融名詞 (例如：EPS、殖利率)：")
if st.button("查詢名詞解釋"):
    if term_input:
        with st.spinner("Gemini 正在思考中..."):
            result = analyzer.ask_gemini_for_term(term_input)
            
        if 'error' in result:
            st.error(result['error'])
        else:
            st.markdown("### 名詞解釋")
            st.write(result.get('definition', 'N/A'))
            st.markdown("### 重要性")
            st.write(result.get('importance', 'N/A'))
            st.markdown("### 生活比喻")
            st.write(result.get('analogy', 'N/A'))
    else:
        st.warning("請輸入金融名詞。")

st.divider()

# 功能2: 查詢股票資訊
st.header("📈 股票基本資訊查詢")

# 使用 session_state 來保存選擇的股票代號
if 'selected_ticker' not in st.session_state:
    st.session_state.selected_ticker = ""
if 'show_results' not in st.session_state:
    st.session_state.show_results = False

# 搜尋股票
search_query = st.text_input("搜尋公司名稱或股票代號：")
if search_query:
    search_results = analyzer.search_stocks(search_query)
    if search_results:
        st.write("搜尋結果：")
        for name, code in search_results:
            if st.button(f"{name} ({code})", key=f"btn_{code}"):
                st.session_state.selected_ticker = code
                st.session_state.show_results = True
                st.rerun()
    else:
        st.warning("找不到符合的股票，請嘗試其他關鍵字。")

# 直接輸入股票代號
direct_input = st.text_input("或直接輸入股票代號 (例如：2330)：")

# 查詢按鈕
if st.button("查詢股票資訊") or st.session_state.show_results:
    # 優先使用搜尋結果選擇的股票代號
    ticker_to_query = st.session_state.selected_ticker if st.session_state.selected_ticker else direct_input
    
    if ticker_to_query:
        with st.spinner(f"正在查詢 {ticker_to_query} 的資訊..."):
            try:
                result = analyzer.get_stock_info(ticker_to_query)
                
                if 'error' in result:
                    st.error(result['error'])
                else:
                    info = result['info']
                    hist = result['history']

                    col1, col2 = st.columns(2)
                    with col1:
                        st.subheader(f"{info.get('longName', ticker_to_query)} ({info.get('symbol', '')})")
                        current_price = info.get('regularMarketPrice')
                        if current_price:
                            st.metric(
                                label="目前股價", 
                                value=f"{current_price:.2f} {info.get('currency', '')}",
                                delta=f"{info.get('regularMarketChange', 0):.2f} ({info.get('regularMarketChangePercent', 0)*100:.2f}%)"
                            )
                        else:
                            st.write("目前股價：無法取得")

                    with col2:
                        st.write(f"**產業：** {info.get('sector', 'N/A')}")
                        st.write(f"**市值：** {info.get('marketCap', 'N/A')}")
                        st.write(f"**網站：** {info.get('website', 'N/A')}")

                    # 顯示最近五日歷史股價圖表
                    if hist is not None and not hist.empty:
                        st.subheader("最近五日股價")
                        st.line_chart(hist['Close'])
                    else:
                        st.warning("無法獲取股價歷史資料。")
            except Exception as e:
                st.error(f"查詢股票資訊時發生錯誤：{str(e)}")
                logger.error(f"查詢股票 {ticker_to_query} 時發生錯誤: {e}")
                traceback.print_exc()
    else:
        st.warning("請輸入股票代號或搜尋公司名稱。")

# 重置選擇的股票代號
if st.button("清除選擇"):
    st.session_state.selected_ticker = ""
    st.session_state.show_results = False
    st.rerun()

st.divider()

# 功能3: 每日市場觀察
st.header("☀️ 每日市場觀察")

if st.button("產生今日觀察報告"):
    with st.spinner("正在產生每日觀察報告..."):
        # 1. TAIEX 指數摘要
        try:
            taiex = yf.Ticker("^TWII")
            taiex_hist = taiex.history(period="1d")
            if not taiex_hist.empty:
                latest_taiex = taiex_hist.iloc[-1]
                taiex_info = (
                    f"台灣加權指數 ({latest_taiex.name.strftime('%Y-%m-%d')}): "
                    f"收盤 {latest_taiex['Close']:.2f}, "
                    f"漲跌 {latest_taiex['Close'] - latest_taiex['Open']:.2f} "
                    f"(開盤: {latest_taiex['Open']:.2f}, 最高: {latest_taiex['High']:.2f}, 最低: {latest_taiex['Low']:.2f})"
                )
                st.subheader("📊 今日台股指數變化摘要")
                st.write(taiex_info)
            else:
                st.warning("無法獲取 TAIEX 指數資料。")
        except Exception as e:
            st.error(f"獲取 TAIEX 資料時發生錯誤: {e}")

        # 2. 從 RSS 獲取新聞並分析
        st.subheader("📰 今日重點新聞分析")
        daily_news = get_rss_news(num_items=3)
        if daily_news:
            for item in daily_news:
                st.markdown(f"**標題：** [{item['title']}]({item['link']})")
                analysis = analyzer.analyze_news_with_gemini(item['title'], item['summary'])
                if 'error' in analysis:
                    st.error(f"  分析錯誤: {analysis['error']}")
                else:
                    st.info(f"  **Gemini 情感判斷：** {analysis.get('sentiment', 'N/A')}")
                    st.caption(f"  **Gemini 新手看法：** {analysis.get('novice_explanation', 'N/A')}")
                    st.markdown("---")
        else:
            st.warning("今日無足夠新聞可供分析。")

        st.success("每日觀察報告產生完畢！")

st.divider()

# 功能4: PTT 股票版文章
st.header("💬 PTT 股票版熱門文章")

if st.button("更新 PTT 文章"):
    with st.spinner("正在獲取 PTT 股票版文章..."):
        try:
            ptt_posts = analyzer.ptt_scraper.get_ptt_stock_posts()
            if ptt_posts:
                for post in ptt_posts:
                    with st.expander(f"{post['title']} (作者: {post['author']}, 日期: {post['date']})"):
                        st.markdown(f"[原文連結]({post['link']})")
                        st.markdown("**文章摘要：**")
                        summary = analyzer.summarize_ptt_post(post['content'])
                        st.write(summary)
                        st.markdown("---")
            else:
                st.warning("無法獲取 PTT 文章。")
        except Exception as e:
            st.error(f"獲取 PTT 文章時發生錯誤: {e}")
            logger.error(f"獲取 PTT 文章時發生錯誤: {e}")
            traceback.print_exc()

# 運行 Streamlit App: 在終端機中輸入 `streamlit run your_script_name.py`

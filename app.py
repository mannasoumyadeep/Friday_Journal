import streamlit as st
import os
import time
from datetime import datetime
import logging
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
import PyPDF2
import re
import pandas as pd
import io
import base64
import tempfile
import requests  # Added this import


class FridayJournals:
    def __init__(self):
        self.url = "https://search.ipindia.gov.in/IPOJournal/Journal/Patent"
        self.temp_dir = tempfile.mkdtemp()  # Create a temporary directory
        self.setup_logging()
        
    def setup_logging(self):
        """Set up logging configuration"""
        if not os.path.exists('logs'):
            os.makedirs('logs')
        log_file = f'logs/friday_journals_{datetime.now().strftime("%Y%m%d")}.log'
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)

    def setup_chrome_driver(self):
        """Setup Chrome driver with appropriate options"""
        options = Options()
        options.add_argument('--headless=new')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        
        # Set download directory
        prefs = {
            'download.default_directory': self.temp_dir,
            'download.prompt_for_download': False,
            'plugins.always_open_pdf_externally': True
        }
        options.add_experimental_option('prefs', prefs)
        
        try:
            service = Service(executable_path='/usr/bin/chromedriver')
            driver = webdriver.Chrome(service=service, options=options)
            return driver
        except Exception as e:
            self.logger.error(f"Error setting up ChromeDriver: {str(e)}")
            st.error("Failed to initialize Chrome driver. Please try again later.")
            raise

    def download_pdfs(self, progress_bar):
        """Download PDFs using Selenium"""
        driver = self.setup_chrome_driver()
        downloaded_files = []
        base_url = "https://search.ipindia.gov.in/IPOJournal/Journal/ViewJournal"
        
        try:
            driver.get(self.url)
            wait = WebDriverWait(driver, 20)
            
            # Get the first row (latest journal)
            row = wait.until(EC.presence_of_element_located((By.XPATH, '//*[@id="Journal"]/tbody/tr[1]')))
            forms = row.find_elements(By.TAG_NAME, "form")
            
            # Extract file names and download using POST requests
            for i, form in enumerate(forms[:2], 1):  # Only first two forms (Part I and II)
                try:
                    filename_input = form.find_element(By.NAME, "FileName")
                    filename = filename_input.get_attribute("value")
                    
                    # Create session and send POST request
                    session = requests.Session()
                    response = session.post(
                        base_url,
                        data={"FileName": filename},
                        stream=True
                    )
                    
                    if response.status_code == 200:
                        # Save the PDF
                        pdf_path = os.path.join(self.temp_dir, f"Part_{i}.pdf")
                        with open(pdf_path, "wb") as f:
                            f.write(response.content)
                        
                        downloaded_files.append(pdf_path)
                        st.success(f"Successfully downloaded Part {i}")
                    else:
                        st.warning(f"Failed to download Part {i}: HTTP {response.status_code}")
                    
                    progress_bar.progress((i * 0.5))
                    
                except Exception as e:
                    self.logger.error(f"Error downloading Part {i}: {str(e)}")
                    st.error(f"Error downloading Part {i}")
                
                time.sleep(2)  # Add delay between downloads
                
            return downloaded_files
            
        except Exception as e:
            self.logger.error(f"Error in download process: {str(e)}")
            st.error("Error accessing the journal website")
            return []
            
        finally:
            driver.quit()

    def extract_application_numbers(self, pdf_files, progress_bar):
        """Extract application numbers from PDFs"""
        application_numbers = []
        pattern = r"Application No\.(\d+)\s*A"
        
        total_files = len(pdf_files)
        for i, pdf_file in enumerate(pdf_files):
            try:
                with open(pdf_file, 'rb') as file:
                    # Display PDF download option
                    pdf_bytes = file.read()
                    pdf_b64 = base64.b64encode(pdf_bytes).decode()
                    st.download_button(
                        label=f"Download Part {i+1}",
                        data=pdf_bytes,
                        file_name=f"Part_{i+1}.pdf",
                        mime="application/pdf"
                    )
                    
                    # Extract application numbers
                    reader = PyPDF2.PdfReader(io.BytesIO(pdf_bytes))
                    st.write(f"Processing Part {i+1} - {len(reader.pages)} pages")
                    
                    for page_num, page in enumerate(reader.pages):
                        text = page.extract_text()
                        matches = re.findall(pattern, text)
                        application_numbers.extend(matches)
                        
                progress_bar.progress(0.5 + ((i + 1) / total_files * 0.5))
                
            except Exception as e:
                self.logger.error(f"Error extracting from {pdf_file}: {str(e)}")
                st.error(f"Error processing Part {i+1}")
                
        return application_numbers

    def create_excel(self, application_numbers):
        """Create Excel file with application numbers"""
        df = pd.DataFrame(application_numbers, columns=['Application Number'])
        excel_buffer = io.BytesIO()
        df.to_excel(excel_buffer, index=False)
        excel_buffer.seek(0)
        return excel_buffer

def main():
    st.set_page_config(
        page_title="Friday Journals",
        page_icon="📚",
        layout="wide"
    )

    st.title("Friday Journals")
    st.write("Automatically download and extract patent application numbers from IPO journals")

    app = FridayJournals()

    if st.button("Start Processing", type="primary"):
        progress_bar = st.progress(0)
        status_text = st.empty()

        try:
            # Step 1: Download PDFs
            status_text.text("Downloading PDFs...")
            pdf_files = app.download_pdfs(progress_bar)
            
            if not pdf_files:
                st.error("Failed to download PDFs. Please try again.")
                return

            # Step 2: Extract Application Numbers
            status_text.text("Extracting application numbers...")
            application_numbers = app.extract_application_numbers(pdf_files, progress_bar)
            
            if not application_numbers:
                st.warning("No application numbers found in the PDFs.")
                return

            # Step 3: Create Excel File
            status_text.text("Creating Excel file...")
            excel_buffer = app.create_excel(application_numbers)
            
            # Success message and download buttons
            st.success(f"✅ Successfully extracted {len(application_numbers)} application numbers!")
            
            # Create download button for Excel
            st.download_button(
                label="📥 Download Excel File",
                data=excel_buffer,
                file_name=f"application_numbers_{datetime.now().strftime('%Y%m%d')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

            # Display sample of extracted numbers
            st.write("Sample of extracted application numbers:")
            sample_df = pd.DataFrame(application_numbers[:10], columns=['Application Number'])
            st.dataframe(sample_df)

        except Exception as e:
            st.error(f"An error occurred: {str(e)}")
            app.logger.error(f"Process failed: {str(e)}")

        finally:
            status_text.text("Processing complete!")

    # Add instructions
    with st.expander("📖 Instructions"):
        st.write("""
        1. Click 'Start Processing' to begin
        2. The app will:
           - Download the latest patent journal PDFs
           - Show download options for each PDF
           - Extract all application numbers
           - Generate an Excel file for download
        3. Download the PDFs and Excel file when processing is complete
        
        Note: This process may take a few minutes to complete.
        """)

if __name__ == "__main__":
    main()
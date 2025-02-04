import streamlit as st
import os
import time
from datetime import datetime, timedelta
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
import requests
import schedule
import json
from pathlib import Path

class FridayJournals:
    def __init__(self):
        self.url = "https://search.ipindia.gov.in/IPOJournal/Journal/Patent"
        self.base_dir = Path("friday_journals_data")
        self.setup_storage()
        self.setup_logging()
        
    def setup_storage(self):
        """Setup storage directories"""
        # Create main directory
        self.base_dir.mkdir(exist_ok=True)
        
        # Create subdirectories
        (self.base_dir / "pdfs").mkdir(exist_ok=True)
        (self.base_dir / "excel").mkdir(exist_ok=True)
        (self.base_dir / "logs").mkdir(exist_ok=True)
        
        # Create metadata file if it doesn't exist
        self.metadata_file = self.base_dir / "metadata.json"
        if not self.metadata_file.exists():
            self.save_metadata({})

    def setup_logging(self):
        """Set up logging configuration"""
        log_file = self.base_dir / "logs" / f"friday_journals_{datetime.now().strftime('%Y%m%d')}.log"
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)

    def get_friday_date(self):
        """Get the current or previous Friday's date"""
        today = datetime.now()
        friday = today - timedelta(days=(today.weekday() - 4) % 7)
        return friday.strftime("%d %B %Y")

    def save_metadata(self, data):
        """Save metadata to JSON file"""
        with open(self.metadata_file, 'w') as f:
            json.dump(data, f)

    def load_metadata(self):
        """Load metadata from JSON file"""
        if self.metadata_file.exists():
            with open(self.metadata_file, 'r') as f:
                return json.load(f)
        return {}

    def process_journal(self, friday_date=None):
        """Process journal for a specific Friday"""
        if friday_date is None:
            friday_date = self.get_friday_date()
            
        # Create directory for this Friday
        date_dir = self.base_dir / "pdfs" / friday_date.replace(" ", "_")
        date_dir.mkdir(exist_ok=True)
        
        # Download and process PDFs
        downloaded_files = self.download_pdfs(date_dir)
        if not downloaded_files:
            return False
            
        # Extract application numbers and create Excel
        application_numbers = []
        pages_without_numbers = []
        
        for pdf_file in downloaded_files:
            numbers, no_number_pages = self.process_pdf(pdf_file)
            application_numbers.extend(numbers)
            pages_without_numbers.extend(no_number_pages)
            
        # Save pages without application numbers
        if pages_without_numbers:
            output_pdf = date_dir / f"{friday_date.replace(' ', '_')}_pages_without_application_numbers.pdf"
            self.create_pdf_without_numbers(pages_without_numbers, output_pdf)
            
        # Create Excel file
        excel_path = self.create_excel(
            application_numbers,
            self.base_dir / "excel" / f"{friday_date} Early and Publication after 18mo (All Jurisdiction).xlsx"
        )
        
        # Update metadata
        metadata = self.load_metadata()
        metadata[friday_date] = {
            'pdf_dir': str(date_dir),
            'excel_file': str(excel_path),
            'total_applications': len(application_numbers),
            'processed_date': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        self.save_metadata(metadata)
        
        return True

    def download_pdfs(self, output_dir):
        """Download PDFs and return list of file paths"""
        driver = None
        downloaded_files = []
        base_url = "https://search.ipindia.gov.in/IPOJournal/Journal/ViewJournal"
        
        try:
            driver = self.setup_chrome_driver()
            wait = WebDriverWait(driver, 20)
            driver.get(self.url)
            
            # Get forms from first row
            row = wait.until(EC.presence_of_element_located((By.XPATH, '//*[@id="Journal"]/tbody/tr[1]')))
            forms = row.find_elements(By.TAG_NAME, "form")
            
            for i, form in enumerate(forms[:2], 1):
                try:
                    filename_input = form.find_element(By.NAME, "FileName")
                    filename = filename_input.get_attribute("value")
                    
                    # Download file
                    session = requests.Session()
                    response = session.post(base_url, data={"FileName": filename}, stream=True)
                    
                    if response.status_code == 200:
                        output_file = output_dir / f"Part_{i}.pdf"
                        with open(output_file, "wb") as f:
                            f.write(response.content)
                        downloaded_files.append(output_file)
                        self.logger.info(f"Downloaded Part {i}")
                    
                except Exception as e:
                    self.logger.error(f"Error downloading Part {i}: {str(e)}")
                
                time.sleep(2)
                
            return downloaded_files
            
        except Exception as e:
            self.logger.error(f"Error in download process: {str(e)}")
            return []
            
        finally:
            if driver:
                driver.quit()

    def process_pdf(self, pdf_path):
        """Process PDF and return application numbers and pages without numbers"""
        application_numbers = []
        pages_without_numbers = []
        pattern = r"Application No\.(\d+)\s*A"
        
        try:
            with open(pdf_path, 'rb') as file:
                reader = PyPDF2.PdfReader(file)
                for page_num, page in enumerate(reader.pages):
                    text = page.extract_text()
                    matches = re.findall(pattern, text)
                    
                    if matches:
                        application_numbers.extend(matches)
                    else:
                        pages_without_numbers.append((pdf_path, page_num))
                        
        except Exception as e:
            self.logger.error(f"Error processing {pdf_path}: {str(e)}")
            
        return application_numbers, pages_without_numbers

    def create_pdf_without_numbers(self, pages_info, output_path):
        """Create PDF with pages that don't have application numbers"""
        writer = PyPDF2.PdfWriter()
        
        try:
            # Group pages by source PDF
            current_pdf = None
            reader = None
            
            for pdf_path, page_num in pages_info:
                if current_pdf != pdf_path:
                    current_pdf = pdf_path
                    reader = PyPDF2.PdfReader(open(current_pdf, 'rb'))
                
                writer.add_page(reader.pages[page_num])
            
            with open(output_path, 'wb') as output_file:
                writer.write(output_file)
                
        except Exception as e:
            self.logger.error(f"Error creating PDF without numbers: {str(e)}")

    def create_excel(self, application_numbers, output_path):
        """Create Excel file with application numbers"""
        try:
            df = pd.DataFrame(application_numbers, columns=['Application Number'])
            df.to_excel(output_path, index=False)
            return output_path
        except Exception as e:
            self.logger.error(f"Error creating Excel file: {str(e)}")
            return None

    def setup_chrome_driver(self):
        """Setup Chrome driver with appropriate options"""
        options = Options()
        options.add_argument('--headless=new')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        
        try:
            service = Service(executable_path='/usr/bin/chromedriver')
            return webdriver.Chrome(service=service, options=options)
        except Exception as e:
            self.logger.error(f"Error setting up ChromeDriver: {str(e)}")
            raise

def friday_job():
    """Job to run every Friday"""
    app = FridayJournals()
    app.process_journal()

def main():
    st.set_page_config(
        page_title="Friday Journals",
        page_icon="📚",
        layout="wide"
    )

    st.title("Friday Journals")
    st.write("Patent Application Number Extractor from IPO Journals")

    app = FridayJournals()
    metadata = app.load_metadata()

    # Show available dates
    if metadata:
        st.subheader("Available Journals")
        for date, info in metadata.items():
            with st.expander(f"📅 {date}"):
                col1, col2 = st.columns(2)
                
                with col1:
                    excel_path = Path(info['excel_file'])
                    if excel_path.exists():
                        with open(excel_path, 'rb') as f:
                            st.download_button(
                                f"📥 Download Excel ({info['total_applications']} applications)",
                                f,
                                file_name=excel_path.name,
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                            )
                
                with col2:
                    pdf_dir = Path(info['pdf_dir'])
                    no_numbers_pdf = pdf_dir / f"{date.replace(' ', '_')}_pages_without_application_numbers.pdf"
                    if no_numbers_pdf.exists():
                        with open(no_numbers_pdf, 'rb') as f:
                            st.download_button(
                                "📥 Download Pages without Application Numbers",
                                f,
                                file_name=no_numbers_pdf.name,
                                mime="application/pdf"
                            )

    # Manual processing button
    if st.button("Process Latest Journal", type="primary"):
        with st.spinner("Processing latest journal..."):
            if app.process_journal():
                st.success("✅ Processing completed successfully!")
                st.rerun()
            else:
                st.error("Failed to process journal. Please try again.")

    # Add instructions
    with st.expander("📖 Instructions"):
        st.write("""
        1. The app automatically processes new journals every Friday night
        2. You can also click 'Process Latest Journal' to run manually
        3. Download files:
           - Excel file contains all extracted application numbers
           - PDF file contains pages without application numbers
        4. All processed journals are archived and available for download
        """)

if __name__ == "__main__":
    # Schedule Friday job
    schedule.every().friday.at("23:00").do(friday_job)
    
    # Run the Streamlit app
    main()
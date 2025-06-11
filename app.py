import streamlit as st
from PIL import Image
import pytesseract
import pandas as pd
import io
import re
import json
import time
import google.generativeai as genai
import PyPDF2
import fitz  # PyMuPDF
from docx import Document
from docx.opc.constants import RELATIONSHIP_TYPE as RT

# Path to tesseract executable (update this path if needed)
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# --------- Google Gemini API Setup ---------
GEMINI_API_KEY = "AIzaSyC1FfBTepadrMv2amd-NfVGeKytcWny5Pw"  # Replace with your actual API key
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-2.0-flash-001')

# --------- MCQ Parser (Flexible) ---------
def parse_flexible_mcqs(text):
    lines = text.splitlines()
    data = []
    question = ""
    options = []
    answer = ""

    question_start_pattern = re.compile(r"^\d+[).\-]?|^(Q\.?|Que\.?|Question)", re.IGNORECASE)
    option_pattern = re.compile(r"^(\(?[a-d1-4A-D]\)?[\).\-])\s*(.*)", re.IGNORECASE)
    answer_pattern = re.compile(r"^(Answer|Ans|Correct Option|Correct Answer)[\s:\-]*", re.IGNORECASE)

    for line in lines:
        line = line.strip()
        if not line:
            continue

        if question_start_pattern.match(line):
            if question and options and answer:
                formatted_options = "\n".join([
                    f"A. {options[0]}" if len(options) > 0 else "",
                    f"B. {options[1]}" if len(options) > 1 else "",
                    f"C. {options[2]}" if len(options) > 2 else "",
                    f"D. {options[3]}" if len(options) > 3 else ""
                ])
                data.append({
                    "Question": question.strip(),
                    "Options": formatted_options,
                    "Answer": answer.strip()
                })
            question = re.sub(question_start_pattern, "", line).strip()
            options = []
            answer = ""
        elif option_pattern.match(line):
            match = option_pattern.match(line)
            if match:
                options.append(match.group(2).strip())
        elif answer_pattern.match(line):
            answer = answer_pattern.sub("", line).strip()
        else:
            if options:
                options[-1] += " " + line
            else:
                question += " " + line

    if question and options and answer:
        formatted_options = "\n".join([
            f"A. {options[0]}" if len(options) > 0 else "",
            f"B. {options[1]}" if len(options) > 1 else "",
            f"C. {options[2]}" if len(options) > 2 else "",
            f"D. {options[3]}" if len(options) > 3 else ""
        ])
        data.append({
            "Question": question.strip(),
            "Options": formatted_options,
            "Answer": answer.strip()
        })

    return data

# --------- Text Extraction ---------
def extract_text_from_pdf_text(file):
    try:
        reader = PyPDF2.PdfReader(file)
        text = ""
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
        return text.strip()
    except Exception:
        return ""

def extract_text_from_pdf_images(file):
    try:
        file.seek(0)
        doc = fitz.open(stream=file.read(), filetype="pdf")
        text = ""
        for page_index in range(len(doc)):
            page = doc[page_index]
            image_list = page.get_images(full=True)
            for img in image_list:
                xref = img[0]
                base_image = doc.extract_image(xref)
                image_bytes = base_image["image"]
                img_pil = Image.open(io.BytesIO(image_bytes))
                text += pytesseract.image_to_string(img_pil) + "\n"
        return text.strip()
    except Exception:
        return ""

def extract_text_from_docx(file):
    try:
        doc = Document(file)
        full_text = []

        # Extract paragraph text
        for para in doc.paragraphs:
            full_text.append(para.text)

        # Extract images and OCR
        ocr_text = ""
        for rel in doc.part.rels.values():
            if rel.reltype == RT.IMAGE:
                image_bytes = rel.target_part.blob
                img_pil = Image.open(io.BytesIO(image_bytes))
                ocr_text += pytesseract.image_to_string(img_pil) + "\n"

        combined_text = "\n".join(full_text) + "\n" + ocr_text
        return combined_text.strip()
    except Exception:
        return ""

def extract_text_from_txt(file):
    return file.read().decode("utf-8")

def extract_text_from_image(file):
    try:
        img = Image.open(file)
        text = pytesseract.image_to_string(img)
        return text.strip()
    except Exception:
        return ""

# --------- Gemini API call ---------
def generate_batch_metadata_gemini(mcqs_batch):
    prompt = (
        "You are an MCQ analysis AI. For each MCQ below, provide:\n"
        "1. Explanation of the correct answer\n"
        "2. Difficulty (Easy, Medium, Hard)\n"
        "3. Tags (2-3 keywords)\n"
        "4. Subject Speciality\n\n"
        "Respond ONLY in a valid JSON array format as:\n"
        "[{\"Explanation\": \"...\", \"Difficulty\": \"...\", \"Tags\": [\"...\"], \"Speciality\": \"...\"}, ...]\n\n"
        "MCQs:\n"
    )
    for i, mcq in enumerate(mcqs_batch):
        prompt += f"\n{i+1}. Q: {mcq['Question']}\nOptions:\n{mcq['Options']}\nAnswer: {mcq['Answer']}\n"

    try:
        response = model.generate_content(prompt)
        prediction_text = response.text.strip()

        json_start = prediction_text.find("[")
        json_end = prediction_text.rfind("]") + 1
        json_text = prediction_text[json_start:json_end]

        return json.loads(json_text)
    except json.JSONDecodeError as e:
        st.error(f"❌ JSON Decode Error: {e}")
        st.code(prediction_text, language="json")
        return [{"Explanation": "", "Difficulty": "", "Tags": [], "Speciality": ""}] * len(mcqs_batch)
    except Exception as e:
        st.error(f"❌ Gemini API Error: {e}")
        return [{"Explanation": "", "Difficulty": "", "Tags": [], "Speciality": ""}] * len(mcqs_batch)

# --------- Streamlit UI ---------
st.title("📘 MCQ Extractor & Enhancer with Google Gemini AI")

uploaded_files = st.file_uploader(
    "📄 Upload PDF, DOCX, TXT or Image files with MCQs",
    type=["pdf", "docx", "txt", "png", "jpg", "jpeg"],
    accept_multiple_files=True
)

if uploaded_files:
    all_text = ""
    for uploaded_file in uploaded_files:
        if uploaded_file.name.lower().endswith(".pdf"):
            # Extract PDF text + images OCR
            text_text = extract_text_from_pdf_text(uploaded_file)
            uploaded_file.seek(0)
            image_text = extract_text_from_pdf_images(uploaded_file)
            combined_text = (text_text + "\n" + image_text).strip()
        elif uploaded_file.name.lower().endswith(".docx"):
            combined_text = extract_text_from_docx(uploaded_file)
        elif uploaded_file.name.lower().endswith(".txt"):
            combined_text = extract_text_from_txt(uploaded_file)
        elif uploaded_file.name.lower().endswith((".png", ".jpg", ".jpeg")):
            combined_text = extract_text_from_image(uploaded_file)
        else:
            st.warning(f"Unsupported file format: {uploaded_file.name}")
            combined_text = ""

        if combined_text:
            all_text += combined_text + "\n\n"

    if not all_text.strip():
        st.warning("⚠ No text could be extracted from the uploaded files.")
        st.stop()

    st.subheader("📝 Extracted Raw Text from all files")
    st.text_area("Raw Extracted Text", all_text.strip(), height=300)

    # Parse MCQs from combined extracted text
    parsed_mcqs = parse_flexible_mcqs(all_text)

    if not parsed_mcqs:
        st.warning("⚠ No valid MCQs found in the extracted text.")
        st.stop()

    # Remove duplicates based on Question text (case-insensitive)
    unique_mcqs_dict = {}
    for mcq in parsed_mcqs:
        key = mcq["Question"].lower()
        if key not in unique_mcqs_dict:
            unique_mcqs_dict[key] = mcq
    unique_mcqs = list(unique_mcqs_dict.values())

    st.subheader(f"⚙ Enhancing {len(unique_mcqs)} unique MCQs with Gemini AI...")

    full_results = []
    batch_size = 25

    for i in range(0, len(unique_mcqs), batch_size):
        batch = unique_mcqs[i:i+batch_size]
        metadata = generate_batch_metadata_gemini(batch)
        
        for base, meta in zip(batch, metadata):  # ✅ Now processes every batch correctly
            options = base["Options"].split("\n")
            options_clean = [opt[3:].strip() for opt in options if opt.strip()]
            options_joined = " //@ ".join(options_clean)
            # Extract correct answer letter from Answer field
            # Try to directly extract option letter (A-D)
            answer_raw = base["Answer"].strip()
            match = re.match(r"^[\(\[]?([A-Da-d1-4])[\)\].\-]?\s*", answer_raw)
            if match:
                ans_key = match.group(1).upper()
                index = ord(ans_key) - ord('A') if ans_key in 'ABCD' else int(ans_key) - 1
                if 0 <= index < len(options_clean):
                    answer_text = options_clean[index]
                else:
                    answer_text = answer_raw  # fallback
            else:
               # Try to match the full answer text directly
                matched_option = next((opt for opt in options_clean if answer_raw.lower() in opt.lower()), "")
                answer_text = matched_option if matched_option else answer_raw


            full_results.append({
                "Question": base["Question"],
                "Options": options_joined,
                "Answer": answer_text,
                "Explanation": meta.get("Explanation", ""),
                "Difficulty": meta.get("Difficulty", ""),
                "Tags": ", ".join(meta.get("Tags", [])),
                "Speciality": meta.get("Speciality", "")
            })
    
        time.sleep(1.5)  # To avoid rate limits



    df = pd.DataFrame(full_results)

    st.success("✅ MCQs enhanced successfully!")
    st.dataframe(df)

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Enhanced MCQs')
    output.seek(0)

    st.download_button(
        label="⬇ Download Enhanced MCQs as Excel",
        data=output,
        file_name="enhanced_mcqs.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
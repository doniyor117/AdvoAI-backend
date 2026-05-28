# app/services/templates.py

CONTRACT_TEMPLATES = {
    "Employment": """
### МЕҲНАТ ШАРТНОМАСИ (Employment Contract Template)

**1. ТАРАФЛАР (PARTIES)**
Иш берувчи (Employer): [Employer Name, Address, Requisites]
Ходим (Employee): [Employee Name, Passport, Address]

**2. ШАРТНОМА МАЗМУНИ (SUBJECT OF THE CONTRACT)**
Ходим [Position] лавозимига ишга қабул қилинади.

**3. ИШ ҲАҚИ ВА ТЎЛОВ ТАРТИБИ (SALARY AND PAYMENT)**
Иш берувчи Ходимга ҳар ойда [Salary Amount] миқдорида иш ҳақи тўлаш мажбуриятини олади.

**4. ИШ ВАҚТИ (WORKING HOURS)**
Иш вақти ҳафтасига [Hours] соат.

**5. ТОМОНЛАРНИНГ ҲУҚУҚ ВА МАЖБУРИЯТЛАРИ (RIGHTS AND OBLIGATIONS)**
(Ушбу қисмда Меҳнат Кодексига мувофиқ ҳуқуқ ва мажбуриятлар баён қилинади)
""",
    "NDA": """
### МАХФИЙЛИК ТЎҒРИСИДАГИ КЕЛИШУВ (NDA Template)

**1. ТАРАФЛАР (PARTIES)**
Ошкор қилувчи тараф (Disclosing Party): [Name]
Қабул қилувчи тараф (Receiving Party): [Name]

**2. МАХФИЙ МАЪЛУМОТ (CONFIDENTIAL INFORMATION)**
Томонлар тижорат сири ва ошкор этилмайдиган маълумотларни ҳимоя қилишга келишадилар.

**3. МАЖБУРИЯТЛАР (OBLIGATIONS)**
Қабул қилувчи тараф маълумотларни учинчи шахсларга ошкор қилмаслик мажбуриятини олади.

**4. ЖАВОБГАРЛИК (LIABILITY)**
Махфийлик бузилган тақдирда, айбдор тараф зарарни қоплайди.
""",
    "Lease": """
### ИЖАРА ШАРТНОМАСИ (Lease Agreement Template)

**1. ТАРАФЛАР (PARTIES)**
Ижарага берувчи (Landlord): [Name, Requisites]
Ижарага олувчи (Tenant): [Name, Requisites]

**2. ШАРТНОМА ПРЕДМЕТИ (SUBJECT OF THE CONTRACT)**
Ижарага берувчи [Address] манзилидаги бинони ижарага олувчига фойдаланиш учун беради.

**3. ТЎЛОВ МИҚДОРИ ВА ТАРТИБИ (PAYMENT)**
Ойлик ижара тўлови [Amount] сўмни ташкил этади. Тўлов ҳар ойнинг [Date]-санасигача амалга оширилади.

**4. МУДДАТ (TERM)**
Шартнома [Start Date] дан [End Date] гача тузилади.
"""
}

def get_template(contract_type: str) -> str:
    """Returns the template for a given contract type, or a general guideline if not found."""
    if not contract_type:
        return "No specific template provided. Please ask the user for details on what kind of contract they want to draft."
    
    # Case insensitive matching
    for key, template in CONTRACT_TEMPLATES.items():
        if key.lower() in contract_type.lower():
            return template
            
    return f"No standard template found for '{contract_type}'. Please use general Uzbekistan legal drafting principles."
